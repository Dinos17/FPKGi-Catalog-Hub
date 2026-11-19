#!/usr/bin/env python3
"""Compare Mocha metadata with FPKGi catalogs without modifying either side."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from .akirabox_catalog_compare import normalize_title, parse_source_metadata
except ImportError:  # Running as a script from the tools directory.
    from akirabox_catalog_compare import normalize_title, parse_source_metadata

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


def _clean_title(value: Any) -> str:
    """Remove a trailing unbracketed version token left in title-only filenames."""
    if not isinstance(value, str):
        return ""
    cleaned = re.sub(r"\s+v?\d+(?:\s+\d+)+$", "", value, flags=re.IGNORECASE)
    return normalize_title(cleaned)


def _title_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _catalog_candidates(
    source: dict[str, Any],
    entries: list[tuple[str, dict[str, Any]]],
    *,
    category: str,
) -> list[tuple[str, dict[str, Any]]]:
    title_id = source.get("title_id")
    title = _clean_title(source.get("title"))
    region = source.get("region")

    if title_id:
        by_id = [
            (url, meta) for url, meta in entries
            if str(meta.get("title_id") or "").strip().upper() == title_id
        ]
        if not by_id:
            return []
        # A title ID identifies the base application, not an individual DLC.
        # Require a sufficiently close package-name match for every category.
        scored = [
            (_title_similarity(title, _clean_title(meta.get("name"))), (url, meta))
            for url, meta in by_id
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if not scored or scored[0][0] < 0.80:
            return []
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
            return []
        return [scored[0][1]]

    if not title:
        return []
    matches = []
    for url, meta in entries:
        if _clean_title(meta.get("name")) != title:
            continue
        if region and _region(meta.get("region")) != region:
            continue
        matches.append((url, meta))
    return matches


def _catalog_filename_metadata(url: str) -> dict[str, Any] | None:
    filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    if filename.casefold() in {"file", "download"} or not filename.lower().endswith((".pkg", ".zip")):
        return None
    return parse_source_metadata(filename)


def _normalized_version(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lstrip("0") or "0"


def _identity_key(item: dict[str, Any], parsed: dict[str, Any]) -> tuple[Any, ...] | None:
    # Include package title as well as title ID: DLCs commonly share a base-game ID.
    title = _clean_title(parsed.get("title"))
    title_id = parsed.get("title_id")
    if not title and not title_id:
        return None
    version = _normalized_version(parsed.get("version"))
    size = item.get("size_bytes")
    if version is None or not isinstance(size, int):
        return None
    return (
        item.get("platform"), item.get("catalog_category"), title, title_id,
        parsed.get("region"), version, tuple(parsed.get("languages") or ()),
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
    counts = {"matched": 0, "metadata_difference": 0, "unverified": 0,
              "new": 0, "ambiguous": 0, "unclassified": 0, "possible_duplicate": 0}
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
        parsed["title"] = _clean_title(parsed.get("title"))
        candidates = _catalog_candidates(parsed, catalogs[target], category=category)
        status = "new"
        reason = "No unique catalog identity match"
        catalog_url = None
        catalog_metadata = None
        differences: list[str] = []
        unavailable: list[str] = []

        if len(candidates) > 1:
            status = "ambiguous"
            reason = "Multiple catalog records match the available title identity"
        elif len(candidates) == 1:
            catalog_url, catalog_metadata = candidates[0]
            catalog_filename = _catalog_filename_metadata(catalog_url)

            source_version = _normalized_version(parsed.get("version"))
            catalog_version = _normalized_version(catalog_metadata.get("version"))
            if source_version is None or catalog_version is None:
                unavailable.append("version")
            elif source_version != catalog_version:
                differences.append("version")

            source_size = item.get("size_bytes")
            catalog_size = catalog_metadata.get("size")
            if not isinstance(source_size, int) or not isinstance(catalog_size, int):
                unavailable.append("size_bytes")
            elif source_size != catalog_size:
                differences.append("size_bytes")

            source_title_id = parsed.get("title_id")
            catalog_title_id = catalog_metadata.get("title_id")
            if source_title_id and catalog_title_id:
                if source_title_id != str(catalog_title_id).strip().upper():
                    differences.append("title_id")
            elif source_title_id or catalog_title_id:
                unavailable.append("title_id")

            source_region = parsed.get("region")
            catalog_region = _region(catalog_metadata.get("region"))
            if source_region and catalog_region:
                if source_region != catalog_region:
                    differences.append("region")
            elif source_region and not catalog_region:
                unavailable.append("region")

            source_min_fw = parsed.get("min_fw")
            catalog_min_fw = catalog_metadata.get("min_fw")
            if source_min_fw is not None:
                if catalog_min_fw is None:
                    unavailable.append("min_fw")
                elif str(source_min_fw).strip() != str(catalog_min_fw).strip():
                    differences.append("min_fw")

            source_languages = sorted(parsed.get("languages") or [])
            catalog_languages = []
            if catalog_filename:
                catalog_languages = sorted(catalog_filename.get("languages") or [])
            elif isinstance(catalog_metadata.get("languages"), list):
                catalog_languages = sorted(str(x).upper() for x in catalog_metadata["languages"])
            if source_languages:
                if not catalog_languages:
                    unavailable.append("languages")
                elif source_languages != catalog_languages:
                    differences.append("languages")

            if differences:
                status = "metadata_difference"
                reason = "Source metadata differs in: " + ", ".join(differences)
            elif unavailable:
                status = "unverified"
                reason = "Catalog identity candidate found, but comparison metadata is unavailable: " + ", ".join(unavailable)
            else:
                status = "matched"
                reason = "Available identity and comparison metadata agree"

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
            "unavailable_metadata": unavailable,
            "reason": reason,
        })

    possible_duplicates = []
    for key, file_ids in duplicate_groups.items():
        if len(file_ids) < 2:
            continue
        possible_duplicates.append({
            "file_ids": file_ids,
            "identity": {
                "platform": key[0], "category": key[1], "title": key[2],
                "title_id": key[3], "region": key[4], "version": key[5],
                "languages": list(key[6]), "min_fw": key[7], "size_bytes": key[8],
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
