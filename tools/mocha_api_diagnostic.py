#!/usr/bin/env python3
"""Read-only diagnostic for the configured Mocha API account.

Only aggregate counts and permission flags are emitted. File names, IDs, paths,
API responses, credentials, and download/share URLs are never printed.
"""
from __future__ import annotations

import json
import os
from email.utils import parsedate_to_datetime
import re
import sys
import time
from collections import deque
from typing import Any
from urllib.parse import quote

import requests

API_BASE = "https://api.mocha.my/api"
TIMEOUT = 30
PAGE_LIMIT = 100
MAX_PAGES_PER_FOLDER = 1000
MAX_FOLDERS = 5000
MAX_TOTAL_PAGES = 1000
MAX_SCAN_SECONDS = 600

MAX_RETRIES = 3
SAFE_REQUEST_ID = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{16,32}-[0-9a-fA-F]{1,8})$"
)
SAFE_ERROR_CODES = frozenset({
    "ROUTE_NOT_FOUND", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND",
    "RATE_LIMITED", "TOO_MANY_REQUESTS", "INTERNAL_ERROR",
})


def safe_http_diagnostics(response: requests.Response) -> dict[str, Any]:
    """Return allowlisted HTTP metadata only; never expose response bodies."""
    headers = response.headers
    details: dict[str, Any] = {
        "http_status": response.status_code,
        "content_type": headers.get("Content-Type", "").split(";", 1)[0][:80] or None,
        "server": headers.get("Server", "")[:80] or None,
        "request_id": None,
        "allow": headers.get("Allow", "")[:120] or None,
        "body_format": "unknown",
        "error_code": None,
    }
    request_id = (
        headers.get("X-Request-Id")
        or headers.get("X-Correlation-Id")
        or headers.get("CF-Ray")
        or ""
    )
    if SAFE_REQUEST_ID.fullmatch(request_id):
        details["request_id"] = request_id

    try:
        payload = response.json()
    except (ValueError, requests.RequestException):
        body = getattr(response, "text", "") or ""
        details["body_format"] = "html" if "<html" in body[:512].lower() else (
            "text" if body else "empty_or_unreadable"
        )
        return details

    if isinstance(payload, dict):
        details["body_format"] = "json_object"
        for key in ("code", "errorCode", "error_code", "type"):
            value = payload.get(key)
            if isinstance(value, str) and value in SAFE_ERROR_CODES:
                details["error_code"] = value
                break
    elif isinstance(payload, list):
        details["body_format"] = "json_array"
    else:
        details["body_format"] = "json_scalar"
    return details


class DiagnosticError(RuntimeError):
    pass


class ScanBudgetReached(DiagnosticError):
    pass


class MochaDiagnostic:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "FPKGi-Catalog-Hub-Mocha-Diagnostic/1.0",
        })

    def get_json(
        self, path: str, params: dict[str, str] | None = None, *,
        allow_not_found: bool = False, deadline: float | None = None
    ) -> dict[str, Any] | None:
        url = API_BASE + path
        for attempt in range(MAX_RETRIES + 1):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise ScanBudgetReached("Global scan time budget reached")
            request_timeout = TIMEOUT if remaining is None else max(1, min(TIMEOUT, remaining))
            try:
                response = self.session.get(url, params=params, timeout=request_timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ScanBudgetReached("Global scan time budget reached") from exc
                if attempt < MAX_RETRIES:
                    delay = min(2 ** attempt, 8)
                    if deadline is not None and time.monotonic() + delay >= deadline:
                        raise ScanBudgetReached("Global scan time budget reached") from exc
                    time.sleep(delay)
                    continue
                raise DiagnosticError(
                    f"Network request failed after retries ({type(exc).__name__})"
                ) from exc
            except requests.RequestException as exc:
                raise DiagnosticError(f"Network request failed ({type(exc).__name__})") from exc

            if response.status_code == 429 and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After", "").strip()
                delay = None
                if retry_after:
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        try:
                            delay = int(parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (TypeError, ValueError, OverflowError):
                            delay = None
                if delay is None:
                    delay = 2 ** attempt
                if delay > 60:
                    response.close()
                    raise DiagnosticError("API rate limit requested a retry delay beyond the safety limit")
                if deadline is not None and time.monotonic() + max(delay, 1) >= deadline:
                    response.close()
                    raise ScanBudgetReached("Global scan time budget reached")
                response.close()
                time.sleep(max(delay, 1))
                continue

            if response.status_code in (404, 405, 501) and allow_not_found:
                response.close()
                return None
            if response.status_code >= 400:
                details = safe_http_diagnostics(response)
                response.close()
                raise DiagnosticError(
                    f"API endpoint {path} returned HTTP {details['http_status']}; "
                    f"safe_response_metadata={json.dumps(details, sort_keys=True)}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                response.close()
                raise DiagnosticError("API returned invalid JSON") from exc
            finally:
                response.close()

            if not isinstance(payload, dict):
                raise DiagnosticError("API returned an unexpected response shape")
            return payload

        raise DiagnosticError("API rate-limit retries were exhausted")

    def list_folder(
        self, path: str, *, remaining_pages: int, deadline: float
    ) -> tuple[int, list[str], int, bool, str | None]:
        cursor = ""
        seen_cursors: set[str] = set()
        file_count = 0
        folders: list[str] = []
        pages = 0

        while True:
            if time.monotonic() >= deadline:
                return file_count, folders, pages, False, "Global scan time budget reached"
            if pages >= MAX_PAGES_PER_FOLDER:
                return file_count, folders, pages, False, "Per-folder pagination safety limit reached"
            if pages >= remaining_pages:
                return file_count, folders, pages, False, "Global page budget reached"
            params = {"path": path, "limit": str(PAGE_LIMIT)}
            if cursor:
                params["cursor"] = cursor
            try:
                payload = self.get_json("/files", params, deadline=deadline)
            except ScanBudgetReached as exc:
                return file_count, folders, pages, False, str(exc)
            pages += 1

            files = payload.get("files")
            child_folders = payload.get("folders")
            if not isinstance(files, list) or not isinstance(child_folders, list):
                raise DiagnosticError("File listing is missing files/folders arrays")

            for item in files:
                if not isinstance(item, dict):
                    raise DiagnosticError("File listing contains malformed metadata")
                required = ("id", "original_name", "size", "path")
                if any(key not in item for key in required):
                    raise DiagnosticError("File metadata is missing required fields")

            for folder in child_folders:
                if not isinstance(folder, str) or not folder.strip("/"):
                    raise DiagnosticError("Folder listing contains an invalid folder name")
                # Preserve whitespace: it may be part of the actual folder name.
                folders.append(folder)

            file_count += len(files)
            has_more = payload.get("hasMore")
            next_cursor = payload.get("nextCursor")
            if has_more is True:
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise DiagnosticError("API says more pages exist but supplied no cursor")
                if next_cursor in seen_cursors:
                    raise DiagnosticError("Pagination cursor repeated; refusing an incomplete scan")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                continue
            if has_more is not False:
                raise DiagnosticError("API did not provide a valid hasMore flag")
            break

        return file_count, folders, pages, True, None

    @staticmethod
    def child_path(parent: str, folder: str) -> str:
        folder = folder.strip("/")
        parent = parent.strip("/")
        if not parent:
            return folder
        # Folder names returned by the listing API are relative to the
        # requested parent. Preserve an already-qualified descendant path,
        # but do not treat a repeated relative name as the parent itself.
        if folder.startswith(parent + "/"):
            return folder
        return parent + "/" + folder

    def run(self) -> dict[str, Any]:
        # The API does not expose a snapshot token, so the report is a traversal
        # diagnostic rather than a transactionally consistent account snapshot.
        deadline = time.monotonic() + MAX_SCAN_SECONDS
        # These account-summary routes may be unavailable on some deployments.
        try:
            me = self.get_json("/auth/me", allow_not_found=True, deadline=deadline)
            profile = self.get_json("/profile/summary", allow_not_found=True, deadline=deadline)
        except ScanBudgetReached as exc:
            return {
                "diagnostic": "mocha_api",
                "read_only": True,
                "authentication": "not_verified",
                "permissions": {
                    "can_use_api": None,
                    "can_create_hotlinks": None,
                    "profile_can_use_api": None,
                    "profile_can_create_hotlinks": None,
                },
                "account_summary_available": False,
                "folder_count": 0,
                "file_count": 0,
                "listing_page_count": 0,
                "scan_complete": False,
                "snapshot_consistency": "not_guaranteed_by_api",
                "details_suppressed": True,
                "error": str(exc),
            }

        permissions = {
            "can_use_api": me.get("canUseApi") if me else None,
            "can_create_hotlinks": me.get("canCreateHotlinks") if me else None,
            "profile_can_use_api": profile.get("canUseApi") if profile else None,
            "profile_can_create_hotlinks": profile.get("canCreateHotlinks") if profile else None,
        }

        queue = deque([""])
        visited: set[str] = set()
        total_files = 0
        total_pages = 0
        folder_count = 0
        scan_error = None

        while queue:
            if time.monotonic() >= deadline:
                scan_error = "Global scan time budget reached"
                break
            if total_pages >= MAX_TOTAL_PAGES:
                scan_error = "Global page budget reached"
                break
            path = queue.popleft()
            if path in visited:
                continue
            if len(visited) >= MAX_FOLDERS:
                scan_error = "Folder safety limit reached"
                break
            visited.add(path)
            folder_count += 1

            files, children, pages, folder_complete, folder_error = self.list_folder(
                path, remaining_pages=MAX_TOTAL_PAGES - total_pages, deadline=deadline
            )
            total_files += files
            total_pages += pages
            for child in children:
                child_path = self.child_path(path, child)
                if child_path not in visited:
                    queue.append(child_path)
            if not folder_complete:
                scan_error = folder_error or "Folder scan incomplete"
                break

        complete = not queue and scan_error is None
        report = {
            "diagnostic": "mocha_api",
            "read_only": True,
            "authentication": "verified_by_file_listing",
            "permissions": permissions,
            "account_summary_available": bool(me or profile),
            "folder_count": folder_count,
            "file_count": total_files,
            "listing_page_count": total_pages,
            # The API does not expose a snapshot token; counts can change while
            # the traversal is running even when every page was visited.
            "scan_complete": complete,
            "snapshot_consistency": "not_guaranteed_by_api",
            "details_suppressed": True,
        }
        if scan_error:
            report["error"] = scan_error
        return report


def main() -> int:
    api_key = os.environ.get("MOCHA_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"diagnostic": "mocha_api", "scan_complete": False,
                          "error": "MOCHA_API_KEY is not configured"}))
        return 2

    try:
        report = MochaDiagnostic(api_key).run()
    except DiagnosticError as exc:
        print(json.dumps({"diagnostic": "mocha_api", "read_only": True,
                          "scan_complete": False, "error": str(exc)}))
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
