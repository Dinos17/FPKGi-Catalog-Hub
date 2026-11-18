#!/usr/bin/env python3
"""Persist owner-approved AkiraBox rejection issues for manual cleanup."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse


MARKER_START = "<!-- akirabox-rejection:v1"
ALLOWED_HOSTS = {"akirabox.to", "akirabox.com"}


def parse_rejection_body(body: str) -> dict:
    text = body or ""
    marker_at = text.find(MARKER_START)
    if marker_at < 0:
        raise ValueError("Issue body is missing the AkiraBox rejection payload.")

    payload_start = marker_at + len(MARKER_START)
    while payload_start < len(text) and text[payload_start].isspace():
        payload_start += 1
    try:
        payload, payload_end = json.JSONDecoder().raw_decode(text, payload_start)
    except json.JSONDecodeError as exc:
        raise ValueError("AkiraBox rejection payload is not valid JSON.") from exc

    suffix = text[payload_end:]
    closing = re.match(r"\s*--!?>\s*", suffix)
    if not closing:
        raise ValueError("AkiraBox rejection payload is missing its closing marker.")

    if not isinstance(payload, dict):
        raise ValueError("AkiraBox rejection payload must be an object.")

    required = ("file_id", "name", "share_url")
    for key in required:
        value = payload.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
            raise ValueError(f"AkiraBox rejection payload has no valid {key}.")

    file_id = str(payload["file_id"]).strip()
    name = str(payload["name"]).strip()
    share_url = str(payload["share_url"]).strip()
    parsed = urlparse(share_url)
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or path_parts != [file_id, "file"]
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Rejected upload URL must be the matching HTTPS AkiraBox file URL.")

    platform = str(payload.get("platform") or "").strip().upper()
    category = str(payload.get("catalog_category") or payload.get("category") or "").strip().lower()

    return {
        "file_id": file_id,
        "name": name,
        "share_url": share_url,
        "platform": platform,
        "catalog_category": category,
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
        saved_record = record
    else:
        existing = report["records"][existing_index]
        existing_issue = existing.get("issue_number")
        incoming_issue = record.get("issue_number")
        # A retry of the same issue is a true no-op. An older issue must not
        # overwrite the provenance or timestamp of a newer rejection.
        if existing_issue == incoming_issue or (
            isinstance(existing_issue, int)
            and isinstance(incoming_issue, int)
            and existing_issue > incoming_issue
        ):
            saved_record = existing
        else:
            report["records"][existing_index] = record
            saved_record = record

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return saved_record


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
