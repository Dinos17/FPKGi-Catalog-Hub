#!/usr/bin/env python3
"""Read-only inventory scanner for a public DeepStorage share.

Fetches listing metadata only; it does not download PKGs, change catalogs, or
perform any upload/delete actions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

API_ROOT = "https://share.deepstorage.dev/public/api/files"
USERNAME_DEFAULT = "fallen"
DEFAULT_PATH = "PS4/Games/"
PAGE_SIZE = 50
MAX_PAGES = 1000
MAX_FILES = 50000
TIMEOUT = (15, 30)
ALLOWED_HOST = "share.deepstorage.dev"


class ScanError(RuntimeError):
    """Listing response cannot be trusted as a complete scan."""


def _validate_file_link(link: Any) -> str:
    if not isinstance(link, str):
        raise ScanError("Listing file_link is not a string")
    parsed = urlparse(link)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.username or parsed.password:
        raise ScanError("Listing contains a file URL outside the approved DeepStorage host")
    if not parsed.path.startswith("/") or not parsed.path.strip("/"):
        raise ScanError("Listing contains an invalid direct file URL")
    return link


class DeepStorageScanner:
    def __init__(
        self,
        username: str = USERNAME_DEFAULT,
        folder_path: str = DEFAULT_PATH,
        session: requests.Session | None = None,
        *,
        max_pages: int = MAX_PAGES,
        max_files: int = MAX_FILES,
    ):
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", username):
            raise ScanError("Invalid public-share username")
        if not isinstance(folder_path, str) or folder_path.startswith("/") or "\\x00" in folder_path:
            raise ScanError("Folder path must be a relative path")
        normalized = folder_path.strip("/")
        if any(part in {".", ".."} for part in normalized.split("/") if part):
            raise ScanError("Folder path contains traversal components")
        self.username = username
        self.folder_path = normalized + ("/" if normalized else "")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "FPKGi-Catalog-Hub-DeepStorage-Scanner/1.0",
        })
        self.max_pages = max_pages
        self.max_files = max_files

    def _get_page(self, offset: int) -> dict[str, Any]:
        url = f"{API_ROOT}/{self.username}"
        try:
            response = self.session.get(
                url,
                params={"path": self.folder_path, "offset": offset, "sort": "date_desc"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ScanError(f"DeepStorage listing request failed ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise ScanError(f"DeepStorage listing returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ScanError("DeepStorage listing response was not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list) or not isinstance(payload.get("folders"), list):
            raise ScanError("DeepStorage listing response has an invalid shape")
        return payload

    def run(self) -> dict[str, Any]:
        offset = 0
        pages = 0
        files: list[dict[str, Any]] = []
        seen_links: set[str] = set()
        while True:
            if pages >= self.max_pages:
                raise ScanError("Listing page safety limit reached; scan incomplete")
            payload = self._get_page(offset)
            pages += 1
            for raw in payload["files"]:
                if not isinstance(raw, dict):
                    raise ScanError("Listing contains malformed file metadata")
                name = raw.get("file_name")
                size = raw.get("file_size")
                direct_link = _validate_file_link(raw.get("file_link"))
                if not isinstance(name, str) or not name.strip():
                    raise ScanError("Listing file has an invalid filename")
                if isinstance(size, bool) or not isinstance(size, (int, float)) or size < 0:
                    raise ScanError("Listing file has an invalid size")
                if direct_link in seen_links:
                    continue
                seen_links.add(direct_link)
                files.append({
                    "name": name,
                    "size_bytes": int(size),
                    "upload_date": raw.get("upload_date") if isinstance(raw.get("upload_date"), str) else None,
                    "folder_path": raw.get("folder_path") if isinstance(raw.get("folder_path"), str) else self.folder_path,
                    "download_url": direct_link,
                    "extension": Path(name).suffix.lower().lstrip(".") or None,
                })
                if len(files) > self.max_files:
                    raise ScanError("File-count safety limit reached; scan incomplete")
            has_more = payload.get("has_more")
            next_offset = payload.get("next_offset")
            if has_more is False:
                break
            if has_more is not True or isinstance(next_offset, bool) or not isinstance(next_offset, int) or next_offset <= offset:
                raise ScanError("Pagination metadata is missing, invalid, or non-advancing")
            offset = next_offset

        observed = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        return {
            "schema_version": 1,
            "scanner": "deepstorage",
            "username": self.username,
            "folder_path": self.folder_path,
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
    parser.add_argument("--username", default=USERNAME_DEFAULT)
    parser.add_argument("--path", default=DEFAULT_PATH, help="Relative folder path in the public share")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = DeepStorageScanner(args.username, args.path).run()
    except ScanError as exc:
        incomplete = {
            "schema_version": 1,
            "scanner": "deepstorage",
            "username": args.username,
            "folder_path": args.path,
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
    print(f"DeepStorage scan complete: {report['file_count']} files across {report['listing_page_count']} listing page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
