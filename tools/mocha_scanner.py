#!/usr/bin/env python3
"""Read-only recursive scanner for an authorized Mocha account.

The scanner reads file metadata only. It never downloads package contents,
creates hotlinks, deletes Mocha files, or modifies catalog JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from email.utils import parsedate_to_datetime
from collections import deque
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://api.mocha.my/api"
TIMEOUT_SECONDS = 30
PAGE_SIZE = 100
MAX_RETRIES = 3
MAX_TOTAL_PAGES = 1000
MAX_FOLDERS = 5000
MAX_SCAN_SECONDS = 600
OPTIONAL_ROUTE_STATUSES = {404, 405, 501}


class ScanError(RuntimeError):
    """Raised when the API response cannot be trusted as a complete scan."""


class ScanBudgetReached(ScanError):
    """Raised when a global safety budget is exhausted."""


class MochaApiClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise ScanError("MOCHA_API_KEY is not configured")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "FPKGi-Catalog-Hub-Mocha-Scanner/1.0",
        })

    def get_json(
        self,
        route: str,
        *,
        params: dict[str, str] | None = None,
        deadline: float,
        optional: bool = False,
    ) -> dict[str, Any] | None:
        # Never allow caller-controlled hosts or absolute URLs.
        if not route.startswith("/") or route.startswith("//") or "://" in route:
            raise ScanError("Refusing a non-relative API route")
        url = API_BASE + route

        for attempt in range(MAX_RETRIES + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ScanBudgetReached("Global scan time budget reached")
            timeout = max(0.001, min(TIMEOUT_SECONDS, remaining))
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=(timeout, timeout),
                    allow_redirects=False,
                    stream=True,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= MAX_RETRIES:
                    raise ScanError(f"Mocha network request failed after retries ({type(exc).__name__})") from exc
                delay = min(2 ** attempt, 8)
                if time.monotonic() + delay >= deadline:
                    raise ScanBudgetReached("Global scan time budget reached") from exc
                time.sleep(delay)
                continue
            except requests.RequestException as exc:
                raise ScanError(f"Mocha request failed ({type(exc).__name__})") from exc

            if 300 <= response.status_code < 400:
                response.close()
                raise ScanError("Mocha API unexpectedly redirected; refusing to forward credentials")

            if response.status_code == 429 and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After", "").strip()
                try:
                    delay = int(retry_after) if retry_after else None
                except ValueError:
                    try:
                        delay = int(parsedate_to_datetime(retry_after).timestamp() - time.time())
                    except (TypeError, ValueError, OverflowError):
                        delay = None
                if delay is None:
                    delay = 2 ** attempt
                # An HTTP-date in the past means retry immediately.
                delay = max(0, delay)
                if delay > 60 or time.monotonic() + delay >= deadline:
                    response.close()
                    raise ScanBudgetReached("Mocha rate-limit retry exceeds the scan safety budget")
                response.close()
                if delay:
                    time.sleep(delay)
                continue

            if 500 <= response.status_code < 600 and attempt < MAX_RETRIES:
                response.close()
                delay = min(2 ** attempt, 8)
                if time.monotonic() + delay >= deadline:
                    raise ScanBudgetReached("Global scan time budget reached")
                time.sleep(delay)
                continue

            if optional and response.status_code in OPTIONAL_ROUTE_STATUSES:
                response.close()
                return None
            if response.status_code >= 400:
                status = response.status_code
                response.close()
                raise ScanError(f"Mocha API route {route} returned HTTP {status}")

            try:
                chunks = []
                for chunk in response.iter_content(chunk_size=1):
                    if time.monotonic() >= deadline:
                        raise ScanBudgetReached("Global scan time budget reached while reading API response")
                    if chunk:
                        chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise ScanBudgetReached("Global scan time budget reached while reading API response")
                payload = json.loads(b"".join(chunks))
            except ScanBudgetReached:
                raise
            except (requests.Timeout, requests.ConnectionError) as exc:
                response.close()
                if attempt >= MAX_RETRIES:
                    raise ScanError(f"Mocha response read failed after retries ({type(exc).__name__})") from exc
                delay = min(2 ** attempt, 8)
                if time.monotonic() + delay >= deadline:
                    raise ScanBudgetReached("Global scan time budget reached") from exc
                time.sleep(delay)
                continue
            except requests.RequestException as exc:
                response.close()
                raise ScanError(f"Mocha response read failed ({type(exc).__name__})") from exc
            except ValueError as exc:
                raise ScanError(f"Mocha API route {route} returned invalid JSON") from exc
            finally:
                response.close()
            if not isinstance(payload, dict):
                raise ScanError(f"Mocha API route {route} returned an unexpected response shape")
            return payload

        raise ScanError("Mocha request retries were exhausted")


class MochaScanner:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        *,
        max_pages: int = MAX_TOTAL_PAGES,
        max_folders: int = MAX_FOLDERS,
        max_seconds: int = MAX_SCAN_SECONDS,
    ):
        self.client = MochaApiClient(api_key, session)
        self.max_pages = max_pages
        self.max_folders = max_folders
        self.max_seconds = max_seconds

    @staticmethod
    def child_path(parent: str, child: str) -> str:
        # The API returns folder names relative to the requested path. Strip
        # separators only; whitespace may be part of a real folder name.
        child = child.strip("/")
        parent = parent.strip("/")
        if not parent:
            return child
        if child.startswith(parent + "/"):
            return child
        return parent + "/" + child

    def _list_folder(
        self, path: str, *, deadline: float, remaining_pages: int
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        cursor = ""
        seen_cursors: set[str] = set()
        files: list[dict[str, Any]] = []
        folders: list[str] = []
        pages = 0

        while True:
            if time.monotonic() >= deadline:
                raise ScanBudgetReached("Global scan time budget reached")
            if pages >= remaining_pages:
                raise ScanBudgetReached("Global listing page budget reached")
            params = {"path": path, "limit": str(PAGE_SIZE)}
            if cursor:
                params["cursor"] = cursor
            payload = self.client.get_json("/files", params=params, deadline=deadline)
            pages += 1

            page_files = payload.get("files")
            page_folders = payload.get("folders")
            if not isinstance(page_files, list) or not isinstance(page_folders, list):
                raise ScanError("Mocha listing is missing files/folders arrays")

            for item in page_files:
                if not isinstance(item, dict):
                    raise ScanError("Mocha listing contains malformed file metadata")
                if not all(key in item for key in ("id", "original_name", "size", "path")):
                    raise ScanError("Mocha file metadata is missing required fields")
                file_id = item["id"]
                name = item["original_name"]
                api_path = item["path"]
                if not isinstance(file_id, (str, int)) or not str(file_id):
                    raise ScanError("Mocha file metadata contains an invalid ID")
                if not isinstance(name, str) or not name:
                    raise ScanError("Mocha file metadata contains an invalid original_name")
                if not isinstance(api_path, str):
                    raise ScanError("Mocha file metadata contains an invalid path")
                size = item["size"]
                if isinstance(size, bool):
                    size_bytes = None
                elif isinstance(size, int) and size >= 0:
                    size_bytes = size
                elif isinstance(size, str) and size.isdecimal():
                    size_bytes = int(size)
                else:
                    size_bytes = None

                created_at = item.get("createdAt", item.get("created_at"))
                parent_id = item.get("parentId", item.get("parent_id"))
                files.append({
                    "id": str(file_id),
                    "name": name,
                    "size_bytes": size_bytes,
                    "api_path": api_path,
                    "folder_path": path,
                    "created_at": created_at if isinstance(created_at, str) else None,
                    "parent_id": str(parent_id) if parent_id is not None else None,
                    "sha256": None,
                    "download_url": None,
                    "hotlink_available": False,
                })

            for folder in page_folders:
                if not isinstance(folder, str) or not folder.strip("/"):
                    raise ScanError("Mocha listing contains an invalid folder name")
                folders.append(folder)

            has_more = payload.get("hasMore")
            next_cursor = payload.get("nextCursor")
            if has_more is True:
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise ScanError("Mocha reports another page but supplied no cursor")
                if next_cursor in seen_cursors:
                    raise ScanError("Mocha pagination cursor repeated; scan is incomplete")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                continue
            if has_more is not False:
                raise ScanError("Mocha listing did not provide a valid hasMore flag")
            return files, folders, pages

    @staticmethod
    def _classify_path(path: str) -> tuple[str | None, str | None]:
        parts = [part.strip().casefold() for part in path.split("/") if part.strip()]
        for index, part in enumerate(parts):
            if part in {"ps4", "ps5"}:
                for candidate in parts[index + 1:]:
                    if candidate in {"games", "dlc"}:
                        return part.upper(), candidate
                return part.upper(), None
        return None, None

    def run(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.max_seconds
        me = self.client.get_json("/auth/me", deadline=deadline, optional=True)
        profile = self.client.get_json("/profile/summary", deadline=deadline, optional=True)

        permissions = {
            "can_use_api": me.get("canUseApi") if me else None,
            "can_create_hotlinks": me.get("canCreateHotlinks") if me else None,
            "profile_can_use_api": profile.get("canUseApi") if profile else None,
            "profile_can_create_hotlinks": profile.get("canCreateHotlinks") if profile else None,
        }

        queue = deque([""])
        visited: set[str] = set()
        seen_file_ids: set[str] = set()
        all_files: list[dict[str, Any]] = []
        folders: list[dict[str, str]] = []
        total_pages = 0

        while queue:
            if time.monotonic() >= deadline:
                raise ScanBudgetReached("Global scan time budget reached")
            path = queue.popleft()
            if path in visited:
                continue
            if len(visited) >= self.max_folders:
                raise ScanBudgetReached("Global folder safety limit reached")
            visited.add(path)
            page_files, child_folders, pages = self._list_folder(
                path, deadline=deadline, remaining_pages=self.max_pages - total_pages
            )
            total_pages += pages
            folders.append({"path": path, "name": path.rsplit("/", 1)[-1] if path else "/"})

            for child in child_folders:
                child_path = self.child_path(path, child)
                if child_path not in visited:
                    queue.append(child_path)

            for item in page_files:
                if item["id"] in seen_file_ids:
                    continue
                seen_file_ids.add(item["id"])
                platform, category = self._classify_path(item["folder_path"])
                item["platform"] = platform
                item["catalog_category"] = category
                item["extension"] = Path(item["name"]).suffix.lower().lstrip(".") or None
                all_files.append(item)

        complete = not queue
        return {
            "scanner": "mocha",
            "read_only": True,
            "authentication": "verified_by_file_listing",
            "permissions": permissions,
            "account_summary_available": bool(me or profile),
            "folder_count": len(folders),
            "file_count": len(all_files),
            "listing_page_count": total_pages,
            "scan_complete": complete,
            "snapshot_consistency": "not_guaranteed_by_api",
            "metadata_only": True,
            "package_binaries_downloaded": False,
            "catalog_write_performed": False,
            "mocha_files_deleted": False,
            "files": all_files,
            "folders": folders,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    api_key = os.environ.get("MOCHA_API_KEY", "").strip()
    if not api_key:
        print("ERROR: MOCHA_API_KEY is not configured", file=sys.stderr)
        return 2
    try:
        report = MochaScanner(api_key).run()
    except ScanError as exc:
        report = {
            "scanner": "mocha",
            "read_only": True,
            "scan_complete": False,
            "catalog_write_performed": False,
            "error": str(exc),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Mocha scan complete: {report['folder_count']} folders, "
        f"{report['file_count']} files, {report['listing_page_count']} pages."
    )
    return 0 if report["scan_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
