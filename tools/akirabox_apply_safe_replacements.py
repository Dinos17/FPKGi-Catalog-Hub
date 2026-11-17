#!/usr/bin/env python3
"""Apply only strictly verified AkiraBox URL replacements to catalog JSON files."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CATALOGS = {
    ("PS4", "games"): Path("ps4/games.json"),
    ("PS4", "dlc"): Path("ps4/dlc.json"),
    ("PS5", "games"): Path("ps5/ps5-games.json"),
    ("PS5", "dlc"): Path("ps5/ps5-dlc.json"),
}


def normalized_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def normalized_version(value: Any) -> str:
    return re.sub(r"^0+(?=\d)", "", str(value or "").strip())


def is_https(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme == "https" and bool(parsed.hostname)
    except ValueError:
        return False


def is_akirabox_url(value: Any) -> bool:
    if not is_https(value):
        return False
    return (urlparse(value).hostname or "").lower() in {"akirabox.to", "akirabox.com"}


def filename_identity(value: Any) -> str:
    name = re.sub(r"\.(pkg|zip)$", "", str(value or ""), flags=re.IGNORECASE)
    name = re.sub(r"\s+\(\d+\)$", "", name)
    return normalized_text(name)


def is_safe_replacement(result: dict[str, Any], source: dict[str, Any],
                        all_files: list[dict[str, Any]]) -> bool:
    proposal = result.get("proposed_replacement")
    source_meta = result.get("source_metadata")
    catalog_meta = result.get("catalog_metadata")
    if (result.get("status") != "outdated" or not isinstance(proposal, dict)
            or not is_https(proposal.get("from")) or not is_akirabox_url(proposal.get("to"))
            or proposal.get("from") != result.get("catalog_url")
            or proposal.get("to") != source.get("share_url")
            or result.get("metadata_differences") != []
            or not isinstance(source_meta, dict) or not isinstance(catalog_meta, dict)):
        return False
    parsed = catalog_meta.get("parsed_filename_metadata")
    if not isinstance(parsed, dict):
        return False
    source_size = source_meta.get("size_bytes", source.get("size_bytes"))
    catalog_size = catalog_meta.get("size_bytes", catalog_meta.get("size"))
    try:
        if int(source_size) <= 0 or int(source_size) != int(catalog_size):
            return False
    except (TypeError, ValueError):
        return False
    if normalized_text(source_meta.get("title")) != normalized_text(parsed.get("title")):
        return False
    if normalized_text(source_meta.get("region")) != normalized_text(parsed.get("region")):
        return False
    if normalized_version(source_meta.get("version")) != normalized_version(parsed.get("version")):
        return False
    if normalized_text(result.get("platform")) != normalized_text(source.get("platform")):
        return False
    if normalized_text(result.get("category")) != normalized_text(source.get("catalog_category", source.get("category"))):
        return False
    identity = filename_identity(source.get("name"))
    if not identity:
        return False
    for other in all_files:
        if str(other.get("id")) == str(source.get("id")):
            continue
        other_identity = filename_identity(other.get("name"))
        if other_identity == identity:
            return False
    return True


def _catalog_record_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key in ("name", "title_id", "region", "version", "size", "min_fw"):
        if key in expected and actual.get(key) != expected.get(key):
            return False
    return True


def apply_safe_replacements(scan: dict[str, Any], comparison: dict[str, Any],
                            catalog_root: Path) -> dict[str, Any]:
    files = scan.get("files", [])
    by_id = {str(item.get("id")): item for item in files}
    results = comparison.get("results", [])
    safe = []
    for result in results:
        source = by_id.get(str(result.get("file_id")))
        if source and is_safe_replacement(result, source, files):
            safe.append((result, source))

    changed: dict[Path, dict[str, Any]] = {}
    applied = []
    skipped = []
    for result, source in safe:
        platform = result.get("platform")
        category = result.get("category")
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
        proposal = result["proposed_replacement"]
        old_url, new_url = proposal["from"], proposal["to"]
        if old_url not in data:
            skipped.append({"file_id": source.get("id"), "reason": "Original catalog URL no longer exists"})
            continue
        if not _catalog_record_matches(data[old_url], result.get("catalog_metadata") or {}):
            skipped.append({"file_id": source.get("id"), "reason": "Catalog metadata changed since comparison"})
            continue
        if new_url in data:
            skipped.append({"file_id": source.get("id"), "reason": "Replacement URL already exists in catalog"})
            continue
        record = data.pop(old_url)
        data[new_url] = record
        applied.append({
            "file_id": source.get("id"),
            "name": source.get("name"),
            "platform": platform,
            "category": category,
            "old_url": old_url,
            "new_url": new_url,
            "catalog_file": relative.as_posix(),
        })

    for path, payload in changed.items():
        if any(item["catalog_file"] == path.relative_to(catalog_root).as_posix() for item in applied):
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
    report = apply_safe_replacements(scan, comparison, args.catalog_root)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Applied {report['applied_count']} safe catalog URL replacement(s).")
    print(f"Skipped {len(report['skipped'])} candidate(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
