#!/usr/bin/env python3
"""Apply only unambiguous, strictly newer AkiraBox package-version upgrades."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from akirabox_catalog_compare import normalize_title

CATALOGS = {
    ("PS4", "games"): Path("ps4/games.json"),
    ("PS4", "dlc"): Path("ps4/dlc.json"),
    ("PS5", "games"): Path("ps5/ps5-games.json"),
    ("PS5", "dlc"): Path("ps5/ps5-dlc.json"),
}
REGIONS = {"US": "USA", "USA": "USA", "EU": "EUR", "EUR": "EUR",
           "JP": "JAP", "JAP": "JAP", "ASIA": "ASIA"}


def normalized_version(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, (str, int, float)):
        return None
    raw = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)+", raw):
        return None
    try:
        return tuple(int(part) for part in raw.split("."))
    except ValueError:
        return None


def is_akirabox_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        return (parsed.scheme == "https"
                and (parsed.hostname or "").lower() in {"akirabox.to", "akirabox.com"}
                and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def _catalog_record_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key in ("name", "title_id", "region", "version", "size", "min_fw"):
        # Compare presence as well as value: a field added after the snapshot
        # is a stale-record change, even when the snapshot omitted that field.
        if actual.get(key) != expected.get(key):
            return False
    return True


def _is_upgrade(result: dict[str, Any], source: dict[str, Any]) -> bool:
    source_meta = result.get("source_metadata")
    catalog_meta = result.get("catalog_metadata")
    if not isinstance(source_meta, dict) or not isinstance(catalog_meta, dict):
        return False
    old_url = result.get("catalog_url")
    new_url = source.get("share_url")
    if not isinstance(old_url, str) or not old_url or old_url == new_url:
        return False
    if not is_akirabox_url(new_url):
        return False

    old_version = normalized_version(catalog_meta.get("version"))
    new_version = normalized_version(source_meta.get("version"))
    if old_version is None or new_version is None or new_version <= old_version:
        return False

    # Never upgrade on title-only similarity. Require a complete, exact identity.
    # Use exactly the same Unicode-aware normalization as the comparator.
    source_title = normalize_title(source_meta.get("title"))
    catalog_title = normalize_title(catalog_meta.get("name"))
    source_id = source_meta.get("title_id")
    catalog_id = catalog_meta.get("title_id")
    source_region = REGIONS.get(str(source_meta.get("region") or "").upper())
    catalog_region = REGIONS.get(str(catalog_meta.get("region") or "").upper())
    if (not source_title or source_title != catalog_title
            or not isinstance(source_id, str) or not source_id.strip()
            or source_id.strip().upper() != str(catalog_id or "").strip().upper()
            or source_region is None or source_region != catalog_region):
        return False

    # A version upgrade may change package size. Other observed metadata
    # disagreements (region, title ID, language, firmware, etc.) are not safe.
    differences = result.get("metadata_differences")
    if not isinstance(differences, list):
        return False
    fields = {item.get("field") for item in differences if isinstance(item, dict)}
    if "version" not in fields or not fields.issubset({"version", "size_bytes"}):
        return False

    size = source_meta.get("size_bytes", source.get("size_bytes"))
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return False
    if source.get("platform") != result.get("platform"):
        return False
    if source.get("catalog_category", source.get("category")) != result.get("category"):
        return False
    return True


def apply_version_upgrades(scan: dict[str, Any], comparison: dict[str, Any],
                           catalog_root: Path) -> dict[str, Any]:
    files = scan.get("files", [])
    by_id = {str(item.get("id")): item for item in files}
    results = comparison.get("results", [])
    # Count every scanned source targeting a catalog record before checking
    # whether that source is otherwise eligible. The comparator reports
    # repeated source identities as "duplicate" rows without catalog_url;
    # inherit their original source's target so they still make it ambiguous.
    target_by_file_id: dict[str, tuple[str, str, str]] = {}
    for result in results:
        file_id = result.get("file_id")
        catalog_url = result.get("catalog_url")
        if file_id is None or not isinstance(catalog_url, str) or not catalog_url:
            continue
        target_by_file_id[str(file_id)] = (
            str(result.get("platform")), str(result.get("category")), catalog_url
        )

    target_counts: dict[tuple[str, str, str], int] = {}
    for result in results:
        file_id = result.get("file_id")
        if file_id is None:
            continue
        file_key = str(file_id)
        target = target_by_file_id.get(file_key)
        if target is None and result.get("status") == "duplicate":
            original_id = result.get("duplicate_of_file_id")
            target = target_by_file_id.get(str(original_id)) if original_id is not None else None
        if target is not None:
            target_counts[target] = target_counts.get(target, 0) + 1

    candidates = []
    for result in results:
        source = by_id.get(str(result.get("file_id")))
        if not source or result.get("status") != "outdated":
            continue
        target = target_by_file_id.get(str(result.get("file_id")))
        if target is None or not _is_upgrade(result, source):
            continue
        candidates.append((result, source, target))

    changed: dict[Path, dict[str, Any]] = {}
    applied, skipped = [], []
    for result, source, target in candidates:
        platform, category, old_url = target
        if target_counts[target] != 1:
            skipped.append({"file_id": source.get("id"),
                            "reason": "More than one scanned file proposes this catalog upgrade"})
            continue
        relative = CATALOGS.get((platform, category))
        if relative is None:
            skipped.append({"file_id": source.get("id"), "reason": "Unsupported catalog target"})
            continue
        path = catalog_root / relative
        if path not in changed:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("DATA"), dict):
                raise ValueError(f"{relative} must contain a DATA object")
            changed[path] = payload
        data = changed[path]["DATA"]
        new_url = source.get("share_url")
        expected = result.get("catalog_metadata") or {}
        if old_url not in data:
            skipped.append({"file_id": source.get("id"),
                            "reason": "Original catalog URL no longer exists"})
            continue
        if not isinstance(data[old_url], dict) or not _catalog_record_matches(data[old_url], expected):
            skipped.append({"file_id": source.get("id"),
                            "reason": "Catalog record changed since comparison"})
            continue
        if new_url in data:
            skipped.append({"file_id": source.get("id"),
                            "reason": "New AkiraBox URL already exists in catalog"})
            continue

        record = dict(data.pop(old_url))
        old_version = record.get("version")
        new_version = result["source_metadata"]["version"]
        record["version"] = new_version
        new_size = result["source_metadata"].get("size_bytes", source.get("size_bytes"))
        if isinstance(new_size, int) and not isinstance(new_size, bool) and new_size > 0:
            record["size"] = new_size
        data[new_url] = record
        applied.append({
            "file_id": source.get("id"), "name": source.get("name"),
            "platform": platform, "category": category,
            "old_version": old_version, "new_version": new_version,
            "old_url": old_url, "new_url": new_url,
            "catalog_file": relative.as_posix(),
        })

    for path, payload in changed.items():
        relative = path.relative_to(catalog_root).as_posix()
        if any(item["catalog_file"] == relative for item in applied):
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")

    return {"applied_count": len(applied), "applied": applied, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--comparison-report", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scan = json.loads(args.scan_report.read_text(encoding="utf-8"))
    comparison = json.loads(args.comparison_report.read_text(encoding="utf-8"))
    report = apply_version_upgrades(scan, comparison, args.catalog_root)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"Applied {report['applied_count']} verified catalog version upgrade(s).")
    print(f"Skipped {len(report['skipped'])} candidate(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
