#!/usr/bin/env python3
"""Read-only recursive inventory of the fixed public DeepStorage share."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

API_ROOT = "https://share.deepstorage.dev/public/api/files"
SHARE_USERNAME = "fallen"
SHARE_URL = "https://deepstorage.dev/fallen"
ROOT_FOLDERS = ("PS4/", "PS5/")
MAX_PAGES = 1000
MAX_FILES = 50000
MAX_FOLDERS = 5000
TIMEOUT = (15, 30)
ALLOWED_HOST = "share.deepstorage.dev"


class ScanError(RuntimeError):
    """Listing response cannot be trusted as a complete scan."""


def _validate_file_link(link: Any) -> str:
    if not isinstance(link, str):
        raise ScanError("Listing file_link is not a string")
    parsed = urlparse(link)
    if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ScanError("Listing contains a file URL outside the approved DeepStorage host")
    if not parsed.path.startswith("/") or not parsed.path.strip("/"):
        raise ScanError("Listing contains an invalid direct file URL")
    return link


def _child_folder_path(raw: Any, parent: str) -> str:
    """Resolve one immediate child folder without allowing traversal or scope escape."""
    value: Any = raw
    if isinstance(raw, dict):
        value = raw.get("path") or raw.get("folder_path")
        if not value:
            value = raw.get("name") or raw.get("folder_name")
    if not isinstance(value, str) or not value.strip():
        raise ScanError("Listing contains a folder without a valid name or path")
    value = value.strip()
    if "\x00" in value or "\\" in value:
        raise ScanError("Listing contains an invalid folder path")

    candidate = value.strip("/")
    parent_base = parent.rstrip("/")
    if (value.startswith("/") or value.endswith("/")) and not candidate.startswith(parent_base + "/"):
        raise ScanError("Folder path does not belong to the requested parent")
    if not candidate:
        raise ScanError("Listing contains an empty folder path")
    if candidate.startswith(parent_base + "/"):
        relative = candidate[len(parent_base) + 1:]
    else:
        relative = candidate
    if "/" in relative:
        raise ScanError("Folder listing did not identify an immediate child folder")
    if relative in {"", ".", ".."}:
        raise ScanError("Listing contains a traversal or empty folder name")
    return parent_base + "/" + relative + "/"


class DeepStorageScanner:
    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        max_pages: int = MAX_PAGES,
        max_files: int = MAX_FILES,
        max_folders: int = MAX_FOLDERS,
    ):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "FPKGi-Catalog-Hub-DeepStorage-Scanner/2.0",
        })
        self.max_pages = max_pages
        self.max_files = max_files
        self.max_folders = max_folders

    def _get_page(self, folder_path: str, offset: int) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{API_ROOT}/{SHARE_USERNAME}",
                params={"path": folder_path, "offset": offset, "sort": "date_desc"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ScanError(f"DeepStorage listing request failed ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise ScanError(f"DeepStorage listing for {folder_path} returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ScanError("DeepStorage listing response was not valid JSON") from exc
        if (not isinstance(payload, dict) or not isinstance(payload.get("files"), list)
                or not isinstance(payload.get("folders"), list)):
            raise ScanError("DeepStorage listing response has an invalid shape")
        return payload

    def run(self) -> dict[str, Any]:
        pending = deque(ROOT_FOLDERS)
        visited_folders: set[str] = set()
        seen_links: set[str] = set()
        files: list[dict[str, Any]] = []
        pages = 0

        while pending:
            folder_path = pending.popleft()
            if folder_path in visited_folders:
                continue
            if not any(folder_path == root or folder_path.startswith(root) for root in ROOT_FOLDERS):
                raise ScanError("Refusing to scan a folder outside the fixed PS4/ and PS5/ roots")
            visited_folders.add(folder_path)
            if len(visited_folders) > self.max_folders:
                raise ScanError("Folder-count safety limit reached; scan incomplete")

            offset = 0
            first_page = True
            while True:
                if pages >= self.max_pages:
                    raise ScanError("Listing page safety limit reached; scan incomplete")
                payload = self._get_page(folder_path, offset)
                pages += 1

                # DeepStorage returns immediate subfolders on the first listing page.
                if first_page:
                    for raw_folder in payload["folders"]:
                        child = _child_folder_path(raw_folder, folder_path)
                        if not any(child.startswith(root) for root in ROOT_FOLDERS):
                            raise ScanError("Folder listing escaped the fixed PS4/ and PS5/ roots")
                        if child not in visited_folders:
                            pending.append(child)
                    first_page = False

                for raw in payload["files"]:
                    if not isinstance(raw, dict):
                        raise ScanError("Listing contains malformed file metadata")
                    name = raw.get("file_name")
                    size = raw.get("file_size")
                    direct_link = _validate_file_link(raw.get("file_link"))
                    if not isinstance(name, str) or not name.strip():
                        raise ScanError("Listing file has an invalid filename")
                    if (isinstance(size, bool) or not isinstance(size, (int, float))
                            or not math.isfinite(size) or size < 0 or int(size) != size):
                        raise ScanError("Listing file has an invalid size")
                    if direct_link in seen_links:
                        continue
                    seen_links.add(direct_link)
                    files.append({
                        "name": name,
                        "size_bytes": int(size),
                        "upload_date": raw.get("upload_date") if isinstance(raw.get("upload_date"), str) else None,
                        "folder_path": folder_path,
                        "download_url": direct_link,
                        "extension": Path(name).suffix.lower().lstrip(".") or None,
                    })
                    if len(files) > self.max_files:
                        raise ScanError("File-count safety limit reached; scan incomplete")

                has_more = payload.get("has_more")
                next_offset = payload.get("next_offset")
                if has_more is False:
                    break
                if (has_more is not True or isinstance(next_offset, bool)
                        or not isinstance(next_offset, int) or next_offset <= offset):
                    raise ScanError("Pagination metadata is missing, invalid, or non-advancing")
                offset = next_offset

        observed = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        ordered_folders = sorted(visited_folders)
        return {
            "schema_version": 2,
            "scanner": "deepstorage",
            "username": SHARE_USERNAME,
            "share_url": SHARE_URL,
            "folder_path": "PS4/ and PS5/ (recursive)",
            "root_folders": list(ROOT_FOLDERS),
            "folder_paths": ordered_folders,
            "folder_count": len(ordered_folders),
            "scan_observed_at": observed,
            "scan_complete": True,
            "read_only": True,
            "metadata_only": True,
            "package_binaries_downloaded": False,
            "catalog_write_performed": False,
            "file_count": len(files),
            "listing_page_count": pages,
            "snapshot_consistency": "not_guaranteed_by_api",
            "files": files,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = DeepStorageScanner().run()
    except ScanError as exc:
        incomplete = {
            "schema_version": 2,
            "scanner": "deepstorage",
            "username": SHARE_USERNAME,
            "share_url": SHARE_URL,
            "root_folders": list(ROOT_FOLDERS),
            "scan_complete": False,
            "read_only": True,
            "catalog_write_performed": False,
            "error": str(exc),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(incomplete, indent=2) + "\n", encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DeepStorage scan complete: {report['file_count']} files in {report['folder_count']} folders across {report['listing_page_count']} listing page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
