#!/usr/bin/env python3
"""Compare Mocha metadata with FPKGi catalogs without modifying either side."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

try:\n    from .akirabox_catalog_compare import normalize_title, parse_source_metadata\nexcept ImportError:  # Running as a script from the tools directory.\n    from akirabox_catalog_compare import normalize_title, parse_source_metadata

CATALOGS = {
    ("PS4", "games"): Path("ps4/games.json"),
    ("PS4", "dlc"): Path("ps4/dlc.json"),
    ("PS5", "games"): Path("ps5/ps5-games.json"),
    ("PS5", "dlc"): Path("ps5/ps5-dlc.json"),
}
REGIONS = {"US": "USA", "USA": "USA", "EU": "EUR", "EUR": "EUR",
           "JP": "JAP", "JAP": "JAP", "ASIA": "ASIA"}


def _load_catalogs(root: Path) -> dict[tuple[str, str], list[tuple[str, dict[str, Any]]]]:
    loaded: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for key, relative in CATALOGS.items():
        path = root / relative
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot safely read catalog {relative}: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("DATA"), dict):
            raise ValueError(f"Catalog {relative} does not contain a DATA object")
        entries = payload["DATA"]
        if any(not isinstance(url, str) or not isinstance(meta, dict)
               for url, meta in entries.items()):
            raise ValueError(f"Catalog {relative} contains malformed entries")
        loaded[key] = list(entries.items())
    return loaded


def _region(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return REGIONS.get(value.strip().upper())


def _catalog_candidates(
    source: dict[str, Any],
    entries: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    title_id = source.get("title_id")
    title = source.get("title") or ""
    region = source.get("region")
    if title_id:
        by_id = [(url, meta) for url, meta in entries
                 if str(meta.get("title_id") or "").upper() == title_id]
        if len(by_id) > 1 and title:
            narrowed = [(url, meta) for url, meta in by_id
                        if normalize_title(meta.get("name")) == title]
            if narrowed:
                by_id = narrowed
        return by_id
    if not title:
        return []
    matches = []
    for url, meta in entries:
        if normalize_title(meta.get("name")) != title:
            continue
        if region and _region(meta.get("region")) != region:
            continue
        matches.append((url, meta))
    return matches


def _identity_key(item: dict[str, Any], parsed: dict[str, Any]) -> tuple[Any, ...] | None:
    # A same-name/size match is only a candidate. Mocha exposes no SHA-256,
    # so this key must never be treated as proof of binary identity.
    identity = parsed.get("title_id") or parsed.get("title")
    if not identity or not parsed.get("version"):
        return None
    size = item.get("size_bytes")
    if not isinstance(size, int):
        return None
    return (
        item.get("platform"), item.get("catalog_category"), identity,
        parsed.get("region"), parsed.get("version"), tuple(parsed.get("languages") or ()),
        parsed.get("min_fw"), size,
    )


def compare_scan(scan: dict[str, Any], catalog_root: Path) -> dict[str, Any]:
    if scan.get("scanner") != "mocha":
        raise ValueError("Input is not a Mocha scan report")
    if scan.get("scan_complete") is not True:
        raise ValueError("Refusing catalog comparison against an incomplete Mocha scan")
    if scan.get("catalog_write_performed") is not False:
        raise ValueError("Unexpected scan report write state")

    catalogs = _load_catalogs(catalog_root)
    counts = {"matched": 0, "metadata_difference": 0, "new": 0,
              "ambiguous": 0, "unclassified": 0, "possible_duplicate": 0}
    rows: list[dict[str, Any]] = []
    duplicate_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)

    for item in scan.get("files", []):
        if not isinstance(item, dict):
            raise ValueError("Mocha scan contains malformed file rows")
        name = item.get("name")
        if not isinstance(name, str) or item.get("extension") != "pkg":
            continue
        platform = item.get("platform")
        category = item.get("catalog_category")
        target = (platform, category)
        if target not in CATALOGS:
            counts["unclassified"] += 1
            rows.append({
                "status": "unclassified", "file_id": item.get("id"),
                "name": name, "folder_path": item.get("folder_path"),
                "reason": "File is not inside a recognized PS4/PS5 Games or DLC folder",
            })
            continue

        parsed = parse_source_metadata(name)
        candidates = _catalog_candidates(parsed, catalogs[target])
        status = "new"
        reason = "No unique catalog identity match"
        catalog_url = None
        catalog_metadata = None
        differences = []

        if len(candidates) > 1:
            status = "ambiguous"
            reason = "Multiple catalog records match the available title identity"
        elif len(candidates) == 1:
            catalog_url, catalog_metadata = candidates[0]
            source_version = parsed.get("version")
            catalog_version = catalog_metadata.get("version")
            if source_version and catalog_version:
                if str(source_version).lstrip("0") != str(catalog_version).lstrip("0"):
                    differences.append("version")
            source_size = item.get("size_bytes")
            catalog_size = catalog_metadata.get("size")
            if isinstance(source_size, int) and isinstance(catalog_size, int) and source_size != catalog_size:
                differences.append("size_bytes")
            if parsed.get("title_id") and catalog_metadata.get("title_id"):
                if parsed["title_id"] != str(catalog_metadata["title_id"]).upper():
                    differences.append("title_id")
            if parsed.get("region") and _region(catalog_metadata.get("region")):
                if parsed["region"] != _region(catalog_metadata.get("region")):
                    differences.append("region")
            if differences:
                status = "metadata_difference"
                reason = "Source metadata differs in: " + ", ".join(differences)
            else:
                status = "matched"
                reason = "Available identity and metadata agree"
        counts[status] += 1

        key = _identity_key(item, parsed)
        if key is not None:
            duplicate_groups[key].append(str(item.get("id")))

        rows.append({
            "status": status,
            "file_id": item.get("id"),
            "name": name,
            "folder_path": item.get("folder_path"),
            "platform": platform,
            "category": category,
            "source_metadata": {
                **parsed,
                "size_bytes": item.get("size_bytes"),
                "sha256": None,
            },
            "catalog_url": catalog_url,
            "catalog_metadata": catalog_metadata,
            "metadata_differences": differences,
            "reason": reason,
        })

    possible_duplicates = []
    for key, file_ids in duplicate_groups.items():
        if len(file_ids) < 2:
            continue
        possible_duplicates.append({
            "file_ids": file_ids,
            "identity": {
                "platform": key[0], "category": key[1], "title_id_or_title": key[2],
                "region": key[3], "version": key[4], "languages": list(key[5]),
                "min_fw": key[6], "size_bytes": key[7],
            },
            "binary_verification": "unavailable_no_sha256_from_mocha",
            "automatic_cleanup": False,
        })

    counts["possible_duplicate"] = len(possible_duplicates)
    return {
        "comparison": "mocha_catalog",
        "read_only": True,
        "scan_complete": True,
        "snapshot_consistency": scan.get("snapshot_consistency"),
        "catalog_write_performed": False,
        "mocha_files_deleted": False,
        "hotlink_creation_available": scan.get("permissions", {}).get("can_create_hotlinks"),
        "automatic_duplicate_cleanup_enabled": False,
        "counts": counts,
        "possible_duplicates": possible_duplicates,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        scan = json.loads(args.scan_report.read_text(encoding="utf-8"))
        if not isinstance(scan, dict):
            raise ValueError("Mocha scan report must be a JSON object")
        report = compare_scan(scan, args.catalog_root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Mocha catalog comparison complete.")
    print(json.dumps(report["counts"], sort_keys=True))
    print(f"Possible duplicate groups: {len(report['possible_duplicates'])} (not auto-cleaned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
