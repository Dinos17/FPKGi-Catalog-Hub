#!/usr/bin/env python3
"""Publish one validated Mocha review snapshot to the private Netlify store.

This is a transitional mirror: the existing public reports-branch writer remains
active until every consumer has moved to the private API.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAX_ENVELOPE_BYTES = 5 * 1024 * 1024


def read_object(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read required private snapshot input {Path(path).name}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Private snapshot input {Path(path).name} must be a JSON object.")
    return value


def post(api_url: str, token: str, catalog_commit: str, resource: str, value: dict) -> None:
    envelope = json.dumps({
        "schema_version": 1,
        "catalog_commit": catalog_commit,
        "resource": resource,
        "value": value,
    }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise SystemExit(f"Private {resource} payload exceeds the 5 MiB function limit.")
    request = urllib.request.Request(
        api_url.rstrip("/") + "?resource=publish",
        data=envelope,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
            if response.status not in (200, 201) or result.get("ok") is not True:
                raise SystemExit(f"Private {resource} publication was not acknowledged.")
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        raise SystemExit(f"Private {resource} publication failed (HTTP {exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Private {resource} publication failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--catalog-commit", required=True)
    for name in ("summary", "inventory", "scan", "comparison", "queue", "candidates", "rejections"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()

    token = os.environ.get("REVIEW_PUBLISH_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("REVIEW_PUBLISH_TOKEN is missing or too short.")
    if len(args.catalog_commit) != 40 or any(ch not in "0123456789abcdef" for ch in args.catalog_commit):
        raise SystemExit("Invalid catalog commit.")

    values = {name: read_object(getattr(args, name)) for name in
              ("summary", "inventory", "scan", "comparison", "queue", "candidates", "rejections")}
    summary = values["summary"]
    observed = summary.get("scan_observed_at")
    if (summary.get("schema_version") != 1 or summary.get("scanner") != "mocha"
            or summary.get("scan_complete") is not True
            or summary.get("catalog_commit") != args.catalog_commit
            or not isinstance(observed, str)):
        raise SystemExit("Summary does not identify a complete scan of the requested catalog commit.")

    for name in ("inventory", "scan", "comparison", "queue", "candidates"):
        if values[name].get("scan_observed_at") != observed:
            raise SystemExit(f"{name} does not match the summary scan timestamp.")
    if values["inventory"].get("file_count") != len(values["inventory"].get("files", [])):
        raise SystemExit("Inventory file count is inconsistent.")
    if values["queue"].get("pending_count") != len(values["queue"].get("entries", [])):
        raise SystemExit("Review queue count is inconsistent.")
    if values["candidates"].get("candidate_count") != len(values["candidates"].get("candidates", [])):
        raise SystemExit("Candidate count is inconsistent.")
    if values["rejections"].get("schema_version") != 1 or not isinstance(values["rejections"].get("records"), list):
        raise SystemExit("Rejection report schema is invalid.")

    # Mutable current pointers are last-write-wins. Publish the summary last;
    # the function rejects any snapshot older than the currently published one.
    order = ("inventory", "scan", "comparison", "queue", "candidates", "rejections", "summary")
    for resource in order:
        post(args.api_url, token, args.catalog_commit, resource, values[resource])
    print("Private snapshot publication acknowledged for all seven resources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
