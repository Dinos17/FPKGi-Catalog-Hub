#!/usr/bin/env python3
"""Stage private scanner inputs in Netlify Blobs for the publisher job."""
from __future__ import annotations
import argparse, json, os, sys, urllib.error, urllib.request
from pathlib import Path

LIMIT = 5 * 1024 * 1024

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--catalog-commit", required=True)
    for name in ("inventory", "scan", "comparison"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    token = os.environ.get("REVIEW_PUBLISH_TOKEN", "")
    if len(token) < 32: raise SystemExit("REVIEW_PUBLISH_TOKEN is missing or too short.")
    if not args.run_id.isdigit() or not re_full_commit(args.catalog_commit):
        raise SystemExit("Invalid run ID or catalog commit.")
    for resource in ("inventory", "scan", "comparison"):
        try:
            value = json.loads(Path(getattr(args, resource)).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Cannot read {resource} input: {exc}") from exc
        envelope = json.dumps({
            "schema_version": 1, "run_id": args.run_id + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
            "catalog_commit": args.catalog_commit, "resource": resource, "value": value,
        }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(envelope) > LIMIT: raise SystemExit(f"{resource} exceeds the 5 MiB private staging limit.")
        request = urllib.request.Request(
            args.api_url.rstrip("/") + "?resource=stage", data=envelope,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
                if response.status not in (200, 201) or result.get("ok") is not True:
                    raise SystemExit(f"Private staging of {resource} was not acknowledged.")
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace")
            raise SystemExit(f"Private staging of {resource} failed (HTTP {exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Private staging of {resource} failed: {exc}") from exc
    print("Three scanner inputs staged in private Netlify storage.")
    return 0

def re_full_commit(value: str) -> bool:
    return len(value) == 40 and all(ch in "0123456789abcdef" for ch in value)

if __name__ == "__main__":
    sys.exit(main())
