#!/usr/bin/env python3
"""Read-only diagnostic for the configured Mocha API account.

Only aggregate counts and permission flags are emitted. File names, IDs, paths,
API responses, credentials, and download/share URLs are never printed.
"""
from __future__ import annotations

import json
import os
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

MAX_RETRIES = 3
SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def safe_http_diagnostics(response: requests.Response) -> dict[str, Any]:
    """Return allowlisted HTTP metadata only; never expose response bodies."""
    headers = response.headers
    details: dict[str, Any] = {
        "http_status": response.status_code,
        "content_type": headers.get("Content-Type", "").split(";", 1)[0][:80] or None,
        "server": headers.get("Server", "")[:80] or None,
        "request_id": (
            headers.get("X-Request-Id")
            or headers.get("X-Correlation-Id")
            or headers.get("CF-Ray")
            or None
        ),
        "allow": headers.get("Allow", "")[:120] or None,
        "body_format": "unknown",
        "error_code": None,
    }
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
            if isinstance(value, str) and SAFE_ERROR_CODE.fullmatch(value):
                details["error_code"] = value
                break
    elif isinstance(payload, list):
        details["body_format"] = "json_array"
    else:
        details["body_format"] = "json_scalar"
    return details


class DiagnosticError(RuntimeError):
    pass


class MochaDiagnostic:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "FPKGi-Catalog-Hub-Mocha-Diagnostic/1.0",
        })

    def get_json(self, path: str, params: dict[str, str] | None = None, *, allow_not_found: bool = False) -> dict[str, Any] | None:
        url = API_BASE + path
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                raise DiagnosticError(f"Network request failed ({type(exc).__name__})") from exc

            if response.status_code == 429 and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = min(max(int(retry_after), 1), 30)
                except ValueError:
                    delay = 2 ** attempt
                response.close()
                time.sleep(delay)
                continue

            if response.status_code == 404 and allow_not_found:
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

    def list_folder(self, path: str) -> tuple[int, list[str], int]:
        cursor = ""
        seen_cursors: set[str] = set()
        file_count = 0
        folders: list[str] = []
        pages = 0

        while True:
            pages += 1
            if pages > MAX_PAGES_PER_FOLDER:
                raise DiagnosticError("Pagination safety limit reached")
            params = {"path": path, "limit": str(PAGE_LIMIT)}
            if cursor:
                params["cursor"] = cursor
            payload = self.get_json("/files", params)

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
                if not isinstance(folder, str) or not folder.strip():
                    raise DiagnosticError("Folder listing contains an invalid folder name")
                folders.append(folder.strip())

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

        return file_count, folders, pages

    @staticmethod
    def child_path(parent: str, folder: str) -> str:
        folder = folder.strip().strip("/")
        parent = parent.strip().strip("/")
        if not parent:
            return folder
        # Folder names returned by the listing API are relative to the
        # requested parent. Preserve an already-qualified descendant path,
        # but do not treat a repeated relative name as the parent itself.
        if folder.startswith(parent + "/"):
            return folder
        return parent + "/" + folder

    def run(self) -> dict[str, Any]:
        # These account-summary routes are present in the desktop client, but
        # may be unavailable on some API deployments. File listing is the
        # capability that matters for this read-only scanner diagnostic.
        me = self.get_json("/auth/me", allow_not_found=True)
        profile = self.get_json("/profile/summary", allow_not_found=True)

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

        while queue:
            path = queue.popleft()
            if path in visited:
                continue
            if len(visited) >= MAX_FOLDERS:
                raise DiagnosticError("Folder safety limit reached")
            visited.add(path)
            folder_count += 1

            files, children, pages = self.list_folder(path)
            total_files += files
            total_pages += pages
            for child in children:
                child_path = self.child_path(path, child)
                if child_path not in visited:
                    queue.append(child_path)

        return {
            "diagnostic": "mocha_api",
            "read_only": True,
            "authentication": "verified_by_file_listing",
            "permissions": permissions,
            "account_summary_available": bool(me or profile),
            "folder_count": folder_count,
            "file_count": total_files,
            "listing_page_count": total_pages,
            "scan_complete": True,
            "details_suppressed": True,
        }


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
