#!/usr/bin/env python3
"""Read-only comparison of an AkiraBox scan with the repository catalogs."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

CATALOGS = {
    ("PS4", "games"): Path("ps4/games.json"),
    ("PS4", "dlc"): Path("ps4/dlc.json"),
    ("PS5", "games"): Path("ps5/ps5-games.json"),
    ("PS5", "dlc"): Path("ps5/ps5-dlc.json"),
}
REGIONS = {"US": "USA", "USA": "USA", "EU": "EUR", "EUR": "EUR",
           "JP": "JAP", "JAP": "JAP", "ASIA": "ASIA"}


def normalize_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\.(pkg|zip)$", "", value.strip(), flags=re.I)
    value = re.sub(r"\s*-?\s*\[[^]]+\]", " ", value)
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


def parse_source_identity(name: str) -> tuple[str, str | None, str | None]:
    stem = re.sub(r"\.(pkg|zip)$", "", name, flags=re.I).strip()
    region_match = re.search(r"\[\s*(US|USA|EU|EUR|JP|JAP|ASIA)\s*\]", stem, re.I)
    version_match = re.search(r"\[\s*v?(\d+(?:\.\d+)+)\s*\]", stem, re.I)
    region = REGIONS.get(region_match.group(1).upper()) if region_match else None
    version = version_match.group(1) if version_match else None
    title = re.sub(r"\s*-?\s*\[[^]]+\]", " ", stem)
    return normalize_title(title), region, version


def _catalog_identity(metadata: dict[str, Any]) -> tuple[str, str | None]:
    title = normalize_title(metadata.get("name"))
    raw_region = metadata.get("region")
    region = REGIONS.get(str(raw_region).upper()) if raw_region is not None else None
    return title, region


def _load_catalog(root: Path, relative: Path) -> dict[str, dict[str, Any]]:
    path = root / relative
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load catalog {path}: {exc}") from exc
    data = payload.get("DATA") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError(f"Catalog {path} must contain a DATA object")
    return data


def compare_scan(scan: dict[str, Any], catalog_root: Path) -> dict[str, Any]:
    catalogs = {key: _load_catalog(catalog_root, path) for key, path in CATALOGS.items()}
    indexes: dict[tuple[str, str], dict[tuple[str, str | None], list[tuple[str, dict[str, Any]]]]] = {}
    for (platform, category), entries in catalogs.items():
        index: dict[tuple[str, str | None], list[tuple[str, dict[str, Any]]]] = {}
        for url, metadata in entries.items():
            if isinstance(metadata, dict):
                identity = _catalog_identity(metadata)
                if identity[0]:
                    index.setdefault(identity, []).append((url, metadata))
        indexes[(platform, category)] = index

    results = []
    counts = {"new": 0, "unchanged": 0, "outdated": 0, "duplicate": 0, "unclassified": 0}
    for item in scan.get("files", []):
        platform, category = item.get("platform"), item.get("catalog_category")
        if (platform, category) not in indexes or not isinstance(item.get("name"), str):
            counts["unclassified"] += 1
            results.append({"status": "unclassified", "file_id": item.get("id"),
                            "name": item.get("name"), "reason": "Missing supported platform/category"})
            continue

        title, region, source_version = parse_source_identity(item["name"])
        if not title:
            counts["unclassified"] += 1
            results.append({"status": "unclassified", "file_id": item.get("id"),
                            "name": item.get("name"), "reason": "Could not normalize filename"})
            continue
        index = indexes[(platform, category)]
        exact = index.get((title, region), [])
        # If the source filename has no region, only accept a unique title match.
        candidates = exact if region else [
            entry for identity, entries in index.items() if identity[0] == title for entry in entries
        ]
        if len(candidates) > 1:
            status, reason = "duplicate", "Multiple catalog entries match title and region"
        elif not candidates:
            status, reason = "new", "No matching title and region in the target catalog"
        else:
            _, metadata = candidates[0]
            old_version = str(metadata.get("version") or "").lstrip("0")
            new_version = str(source_version or "").lstrip("0")
            old_size, new_size = metadata.get("size"), item.get("size_bytes")
            differences = []
            if source_version and old_version and new_version != old_version:
                differences.append("version differs")
            if isinstance(new_size, int) and isinstance(old_size, int) and new_size != old_size:
                differences.append("file size differs")
            if differences:
                status, reason = "outdated", ", ".join(differences)
            else:
                status, reason = "unchanged", "Matching title/region; available version and size metadata agree"
        counts[status] += 1
        results.append({
            "status": status, "file_id": item.get("id"), "name": item.get("name"),
            "platform": platform, "category": category, "region": region,
            "version": source_version, "share_url": item.get("share_url"), "reason": reason,
        })

    expected = {(platform, category) for platform in ("PS4", "PS5") for category in ("games", "dlc")}
    discovered = set()
    for folder in scan.get("folders", []):
        path = folder.get("path", [])
        if len(path) >= 2 and str(path[1]).casefold() in {"games", "dlc"}:
            discovered.add((str(path[0]).upper(), str(path[1]).casefold()))
    complete = not scan.get("errors") and expected.issubset(discovered)
    return {
        "scanner": "akirabox_catalog_comparison",
        "read_only": True,
        "catalog_write_performed": False,
        "scan_complete_for_all_categories": complete,
        "missing_check_deferred": not complete,
        "counts": counts,
        "results": results,
        "missing_catalog_entries": [],
        "note": ("All four source categories were discovered; missing-source comparison can be enabled."
                 if complete else
                 "At least one of PS4/Games, PS4/DLC, PS5/Games, PS5/DLC is not confirmed. "
                 "Missing catalog entries are intentionally not inferred from this partial scan."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        scan = json.loads(args.scan_report.read_text(encoding="utf-8"))
        report = compare_scan(scan, args.catalog_root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
