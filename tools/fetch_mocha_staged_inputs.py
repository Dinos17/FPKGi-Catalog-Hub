#!/usr/bin/env python3
"""Fetch this run's staged scanner inputs from private Netlify storage."""
from __future__ import annotations
import argparse, json, os, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    token = os.environ.get("REVIEW_PUBLISH_TOKEN", "")
    if len(token) < 32: raise SystemExit("REVIEW_PUBLISH_TOKEN is missing or too short.")
    run_id = args.run_id + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    for resource in ("inventory", "scan", "comparison"):
        query = urllib.parse.urlencode({"resource":"machine", "name":"staged_" + resource, "run_id":run_id})
        request = urllib.request.Request(args.api_url.rstrip("/") + "?" + query,
            headers={"Authorization":"Bearer " + token, "Accept":"application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
                if response.status != 200: raise SystemExit(f"Private staged {resource} read returned HTTP {response.status}.")
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace")
            raise SystemExit(f"Private staged {resource} read failed (HTTP {exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Private staged {resource} read failed: {exc}") from exc
        if not isinstance(result, dict) or result.get("resource") != "staged_" + resource or not isinstance(result.get("value"), dict):
            raise SystemExit(f"Private staged {resource} response is malformed.")
        filename = {"inventory":"mocha-public-inventory.json","scan":"mocha-scan-report.json","comparison":"mocha-comparison-report.json"}[resource]
        (output / filename).write_text(json.dumps(result["value"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Fetched this run's three scanner inputs from private Netlify storage.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
