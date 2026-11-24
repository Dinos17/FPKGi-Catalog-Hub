#!/usr/bin/env python3
"""Submit a validated GitHub issue decision to the private Netlify review API."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from tools.mocha_decision_key import decision_key
from pathlib import Path


def main() -> int:
    event_path = Path(os.environ["EVENT_PATH"])
    event = json.loads(event_path.read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    actor = (issue.get("user") or {}).get("login")
    if actor != "Dinos17":
        raise SystemExit("Only the repository owner may record review decisions.")
    body = issue.get("body") or ""
    match = re.search(r"<!-- FPKGI_REVIEW_DECISION\s*(\{.*?\})\s*-->", body, re.S)
    if not match:
        raise SystemExit("Missing structured review decision payload.")
    payload = json.loads(match.group(1))
    if payload.get("schema_version") != 1 or payload.get("action") not in {"keep", "reject", "resolve"}:
        raise SystemExit("Unsupported review decision payload.")
    for key in ("name", "platform", "category", "created_at"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise SystemExit(f"Decision identity field {key} is missing.")
    if payload["platform"] not in {"PS4", "PS5"} or payload["category"] not in {"games", "dlc"}:
        raise SystemExit("Decision platform/category is unsupported.")
    try:
        uploaded = datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("Decision upload timestamp is invalid.") from exc
    if uploaded.tzinfo is None:
        raise SystemExit("Decision upload timestamp must include a timezone.")
    payload["decision_key"] = decision_key(
        payload["platform"], payload["category"], payload["name"], payload["created_at"]
    )
    payload["issue_number"] = issue.get("number")
    payload["decided_by"] = actor
    api_url = os.environ.get("REVIEW_API_URL", "").rstrip("/")
    token = os.environ.get("REVIEW_PUBLISH_TOKEN", "")
    if not api_url.startswith("https://") or len(token) < 32:
        raise SystemExit("Private review API configuration is missing.")
    request = urllib.request.Request(
        api_url + "?resource=decision",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
            if response.status != 201 or result.get("ok") is not True:
                raise SystemExit(f"Private decision API returned an unexpected response: HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        raise SystemExit(f"Private decision API rejected the record (HTTP {exc.code}): {detail}") from exc
    print("Review decision recorded in private Netlify storage.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SystemExit(f"Invalid decision event: {exc}") from exc
