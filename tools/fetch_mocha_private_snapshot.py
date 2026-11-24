#!/usr/bin/env python3
"""Fetch allowlisted private Mocha review resources from Netlify."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def fetch(api_url: str, token: str, name: str) -> dict:
    query = urllib.parse.urlencode({"resource": "machine", "name": name})
    request = urllib.request.Request(
        api_url.rstrip("/") + "?" + query,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.status != 200:
                raise SystemExit(f"Private {name} read returned HTTP {response.status}.")
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        raise SystemExit(f"Private {name} read failed (HTTP {exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Private {name} read failed: {exc}") from exc
    if not isinstance(result, dict) or result.get("resource") != name:
        raise SystemExit(f"Private {name} response has an invalid envelope.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    token = os.environ.get("REVIEW_PUBLISH_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("REVIEW_PUBLISH_TOKEN is missing or too short.")
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    for name in ("summary", "inventory", "scan", "comparison", "queue", "candidates", "rejections"):
        envelope = fetch(args.api_url, token, name)
        value = envelope.get("value")
        if not isinstance(value, dict):
            raise SystemExit(f"Private {name} payload is not an object.")
        (output / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    decisions = fetch(args.api_url, token, "decisions")
    records = decisions.get("records")
    if not isinstance(records, list):
        raise SystemExit("Private decisions response has an invalid records list.")
    (output / "decisions.json").write_text(
        json.dumps({"schema_version": 1, "records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("Fetched seven private reports and the private decision ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
