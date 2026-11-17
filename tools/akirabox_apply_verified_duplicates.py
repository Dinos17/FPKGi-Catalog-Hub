#!/usr/bin/env python3
"""Apply only fully verified AkiraBox duplicate decisions to catalog JSON files.

A source duplicate group is actionable only when the comparison report marks
every duplicate as verified_identical and the scan contains distinct,
timezone-aware creation timestamps for every copy. The catalog is changed only
when the group's original source maps to one current catalog record and that
record still matches the comparison snapshot. AkiraBox files are never deleted.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CATALOGS = {
    ("PS4", "games"): Path("ps4/games.json"),
    ("PS4", "dlc"): Path("ps4/dlc.json"),
    ("PS5", "games"): Path("ps5/ps5-games.json"),
    ("PS5", "dlc"): Path("ps5/ps5-dlc.json"),
}


def parse_created_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def is_akirabox_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() in {"akirabox.to", "akirabox.com"}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def record_matches(actual: Any, expected: Any) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    for key in ("name", "title_id", "region", "version", "size", "min_fw"):
        if key in expected and actual.get(key) != expected.get(key):
            return False
    return True


def _load_catalog(catalog_root: Path, relative: Path,
                  changed: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    path = catalog_root / relative
    if path not in changed:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("DATA"), dict):
            raise ValueError(f"{relative} must contain a DATA object")
        changed[path] = payload
    return changed[path]["DATA"]


def apply_verified_duplicates(scan: dict[str, Any], comparison: dict[str, Any],
                               catalog_root: Path) -> dict[str, Any]:
    files = scan.get("files", [])
    by_id = {str(item.get("id")): item for item in files if isinstance(item, dict)}
    results = comparison.get("results", [])
    result_by_id = {
        str(row.get("file_id")): row for row in results
        if isinstance(row, dict) and row.get("file_id") is not None
    }

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        if (isinstance(row, dict) and row.get("status") == "duplicate"
                and row.get("duplicate_of_file_id") is not None):
            groups.setdefault(str(row["duplicate_of_file_id"]), []).append(row)

    changed: dict[Path, dict[str, Any]] = {}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for original_id, duplicate_rows in groups.items():
        original = by_id.get(original_id)
        group_rows = [original] + [by_id.get(str(row.get("file_id"))) for row in duplicate_rows]
        if original is None or any(item is None for item in group_rows):
            skipped.append({"original_file_id": original_id, "reason": "A duplicate group member is missing from the scan"})
            continue

        decisions = [row.get("automatic_decision") for row in duplicate_rows]
        if (not decisions or any(not isinstance(decision, dict) for decision in decisions)
                or any(decision != decisions[0] for decision in decisions)):
            skipped.append({"original_file_id": original_id, "reason": "Duplicate group has no consistent automatic decision"})
            continue
        decision = decisions[0]
        selected_id = str(decision.get("selected_file_id") or "")
        selected = by_id.get(selected_id)
        if selected is None or selected not in group_rows:
            skipped.append({"original_file_id": original_id, "reason": "Selected upload is not a member of the duplicate group"})
            continue

        timestamps = [parse_created_at(item.get("created_at")) for item in group_rows]
        hashes_valid = all(
            row.get("binary_verification", {}).get("status") == "verified_identical"
            and isinstance(row.get("binary_verification", {}).get("original_sha256"), str)
            and re.fullmatch(r"[a-f0-9]{64}", row["binary_verification"]["original_sha256"])
            and row.get("binary_verification", {}).get("original_sha256")
                == row.get("binary_verification", {}).get("duplicate_sha256")
            for row in duplicate_rows
        )
        if (not hashes_valid
                or any(timestamp is None for timestamp in timestamps)
                or len(set(timestamps)) != len(timestamps)):
            skipped.append({"original_file_id": original_id, "reason": "Hashes or creation timestamps failed verification"})
            continue
        newest = max(zip(timestamps, group_rows), key=lambda pair: pair[0])[1]
        if str(newest.get("id")) != selected_id:
            skipped.append({"original_file_id": original_id, "reason": "Automatic selection is not the newest verified upload"})
            continue

        primary = result_by_id.get(original_id)
        if not isinstance(primary, dict) or primary.get("status") not in {"matched", "outdated"}:
            skipped.append({"original_file_id": original_id, "reason": "Original upload does not map to one catalog entry"})
            continue
        platform, category = primary.get("platform"), primary.get("category")
        relative = CATALOGS.get((platform, category))
        if relative is None:
            skipped.append({"original_file_id": original_id, "reason": "Unsupported catalog target"})
            continue

        catalog_url = primary.get("catalog_url")
        expected = primary.get("catalog_metadata")
        if not isinstance(catalog_url, str) or not isinstance(expected, dict):
            skipped.append({"original_file_id": original_id, "reason": "Catalog match is incomplete"})
            continue
        new_url = selected.get("share_url")
        if not is_akirabox_url(new_url):
            skipped.append({"original_file_id": original_id, "reason": "Selected upload has no valid AkiraBox share URL"})
            continue

        data = _load_catalog(catalog_root, relative, changed)
        if catalog_url not in data or not record_matches(data[catalog_url], expected):
            skipped.append({"original_file_id": original_id, "reason": "Catalog entry changed or no longer matches the comparison"})
            continue

        source_urls = {
            item.get("share_url") for item in group_rows
            if is_akirabox_url(item.get("share_url"))
        }
        existing_source_urls = sorted(url for url in source_urls if url in data)
        source_records = [data[url] for url in existing_source_urls]
        if any(record != data[catalog_url] for record in source_records):
            skipped.append({"original_file_id": original_id, "reason": "Existing duplicate catalog records are not identical"})
            continue
        if new_url in data and new_url != catalog_url and data[new_url] != data[catalog_url]:
            skipped.append({"original_file_id": original_id, "reason": "Selected AkiraBox URL already has a different catalog record"})
            continue

        record = data[catalog_url]
        # Remove only catalog keys that point to verified members of this exact
        # duplicate group and contain the exact same metadata record.
        removed_urls = []
        for url in existing_source_urls:
            if url != new_url and url != catalog_url:
                del data[url]
                removed_urls.append(url)
        if catalog_url != new_url:
            if new_url in data:
                del data[catalog_url]
            else:
                del data[catalog_url]
                data[new_url] = record
        applied.append({
            "original_file_id": original_id,
            "selected_file_id": selected_id,
            "selected_file_name": selected.get("name"),
            "platform": platform,
            "category": category,
            "catalog_file": relative.as_posix(),
            "old_catalog_url": catalog_url,
            "new_catalog_url": new_url,
            "removed_duplicate_catalog_urls": removed_urls,
        })

    for path, payload in changed.items():
        relative = path.relative_to(catalog_root).as_posix()
        if any(item["catalog_file"] == relative for item in applied):
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "applied_count": len(applied),
        "applied": applied,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "catalog_write_performed": bool(applied),
        "akirabox_files_deleted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--comparison-report", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scan = json.loads(args.scan_report.read_text(encoding="utf-8"))
    comparison = json.loads(args.comparison_report.read_text(encoding="utf-8"))
    report = apply_verified_duplicates(scan, comparison, args.catalog_root)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Applied {report['applied_count']} verified duplicate cleanup(s).")
    print(f"Skipped {report['skipped_count']} unsafe or unverified group(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
