#!/usr/bin/env python3
"""Persist owner-approved AkiraBox rejection issues for manual cleanup."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


MARKER = re.compile(
    r"<!--\\s*akirabox-rejection:v1\\s*(\\{.*?\\})\\s*-->",
    re.DOTALL,
)
ALLOWED_HOSTS = {"akirabox.to", "akirabox.com"}


def parse_rejection_body(body: str) -> dict:
    match = MARKER.search(body or "")
    if not match:
        raise ValueError("Issue body is missing the AkiraBox rejection payload.")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("AkiraBox rejection payload is not valid JSON.") from exc

    required = ("file_id", "name", "share_url")
    if not isinstance(payload, dict):
        raise ValueError("AkiraBox rejection payload must be an object.")
    for key in required:
        value = payload.get(key)
        if not isinstance(value, (str, int)) or not str(value).strip():
            raise ValueError(f"AkiraBox rejection payload has no valid {key}.")

    parsed = urlparse(str(payload["share_url"]))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Rejected upload URL must be an HTTPS AkiraBox file URL.")

    return {
        "file_id": str(payload["file_id"]),
        "name": str(payload["name"]),
        "share_url": str(payload["share_url"]),
        "scan_generated_at": str(payload.get("scan_generated_at") or ""),
    }


def record_issue(
    event: dict,
    report_path: Path,
    expected_actor: str,
    now: datetime | None = None,
) -> dict:
    issue = event.get("issue") if isinstance(event, dict) else None
    if not isinstance(issue, dict):
        raise ValueError("GitHub event does not contain an issue.")
    actor = (issue.get("user") or {}).get("login")
    title = issue.get("title") or ""
    if actor != expected_actor:
        raise ValueError("Only the repository owner may record AkiraBox rejections.")
    if not title.startswith("AkiraBox rejection:"):
        raise ValueError("Issue title is not an AkiraBox rejection.")
    if issue.get("state") == "closed":
        raise ValueError("Closed issues cannot create rejection records.")

    record = parse_rejection_body(issue.get("body") or "")
    timestamp = now or datetime.now(timezone.utc)
    record.update(
        {
            "status": "manual_deletion_required",
            "rejected_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "issue_number": issue.get("number"),
            "issue_url": issue.get("html_url"),
        }
    )

    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("schema_version") != 1 or not isinstance(report.get("records"), list):
            raise ValueError("Existing rejection report has an unsupported format.")
    else:
        report = {"schema_version": 1, "records": []}

    key = (record["file_id"], record["share_url"])
    existing_index = next(
        (
            index
            for index, item in enumerate(report["records"])
            if isinstance(item, dict)
            and (str(item.get("file_id")), item.get("share_url")) == key
        ),
        None,
    )
    if existing_index is None:
        report["records"].append(record)
    else:
        report["records"][existing_index] = {
            **report["records"][existing_index],
            **record,
        }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-path", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--expected-actor", required=True)
    args = parser.parse_args()

    event = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
    record = record_issue(
        event,
        Path(args.report_path),
        expected_actor=args.expected_actor,
    )
    print(f"Recorded rejection for {record['name']} (file ID {record['file_id']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
