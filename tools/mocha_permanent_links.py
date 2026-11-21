#!/usr/bin/env python3
"""Create Mocha permanent links for explicitly selected file IDs.

This utility is intentionally opt-in: it does not create links during routine
scans, publish tokens, resolve URLs, or modify catalog JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

API_URL = "https://api.mocha.my/api/permanent-links"
TIMEOUT_SECONDS = 30
MAX_FILE_IDS = 100


class PermanentLinkError(RuntimeError):
    """Raised when Mocha cannot confirm permanent-link creation."""


class MochaPermanentLinkClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise PermanentLinkError("MOCHA_API_KEY is not configured")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "FPKGi-Catalog-Hub-Mocha-Permanent-Link/1.0",
        })

    def create(self, file_id: str) -> dict[str, Any]:
        if not isinstance(file_id, str) or not file_id.strip():
            raise PermanentLinkError("Invalid Mocha file ID")

        # Do not retry POST requests: a timeout after server-side creation could
        # otherwise create multiple permanent links for the same file.
        try:
            response = self.session.post(
                API_URL,
                json={"fileId": file_id},
                timeout=TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise PermanentLinkError(
                f"Mocha permanent-link request failed ({type(exc).__name__}); "
                "creation status may be unknown"
            ) from exc

        if 300 <= response.status_code < 400:
            raise PermanentLinkError(
                "Mocha permanent-link API unexpectedly redirected; refusing "
                "to forward credentials"
            )
        if response.status_code >= 400:
            raise PermanentLinkError(
                f"Mocha permanent-link API returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise PermanentLinkError("Mocha returned invalid JSON") from exc

        link = payload.get("link") if isinstance(payload, dict) else None
        if not isinstance(link, dict):
            raise PermanentLinkError("Mocha response is missing the link object")
        token = link.get("token")
        returned_file_id = link.get("file_id")
        permanent = link.get("is_permanent_link")
        if not isinstance(token, str) or not token.strip():
            raise PermanentLinkError("Mocha response is missing a valid link token")
        if str(returned_file_id) != file_id:
            raise PermanentLinkError("Mocha response file ID does not match the request")
        if permanent is not True:
            raise PermanentLinkError("Mocha did not confirm this is a permanent link")

        return {
            "file_id": file_id,
            "token": token,
            "is_permanent_link": True,
            "creation_confirmed": True,
        }


def create_links(file_ids: list[str], client: MochaPermanentLinkClient) -> dict[str, Any]:
    normalized = list(dict.fromkeys(value.strip() for value in file_ids if value.strip()))
    if not normalized:
        raise PermanentLinkError("No Mocha file IDs were supplied")
    if len(normalized) > MAX_FILE_IDS:
        raise PermanentLinkError(f"Refusing more than {MAX_FILE_IDS} link creations in one run")

    results = []
    for file_id in normalized:
        try:
            results.append({"status": "created", **client.create(file_id)})
        except PermanentLinkError as exc:
            results.append({"status": "error", "file_id": file_id, "error": str(exc)})

    return {
        "service": "mocha",
        "operation": "create_permanent_links",
        "requested_count": len(normalized),
        "created_count": sum(row["status"] == "created" for row in results),
        "failed_count": sum(row["status"] == "error" for row in results),
        "catalog_write_performed": False,
        "public_url_resolved": False,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-id", action="append", required=True, dest="file_ids",
                        help="Mocha file ID; may be supplied more than once")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    api_key = os.environ.get("MOCHA_API_KEY", "").strip()
    if not api_key:
        print("ERROR: MOCHA_API_KEY is not configured", file=sys.stderr)
        return 2

    try:
        report = create_links(args.file_ids, MochaPermanentLinkClient(api_key))
    except PermanentLinkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Permanent-link creation: {report['created_count']} created, "
        f"{report['failed_count']} failed. Tokens are in the specified output file."
    )
    return 0 if report["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
