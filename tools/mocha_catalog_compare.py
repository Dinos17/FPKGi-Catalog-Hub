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
    cleaned = re.sub(r"[_\s-]+v?\d+(?:[._\s-]+\d+)+$", "", value, flags=re.IGNORECASE)
    return normalize_title(cleaned)


def _title_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _build_catalog_indexes(
    catalogs: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]],
) -> dict[tuple[str, str], dict[str, Any]]:
    indexes = {}
    for key, entries in catalogs.items():
        by_id: dict[str, list[tuple[str, dict[str, Any], str, str | None]]] = defaultdict(list)
        by_title: dict[str, list[tuple[str, dict[str, Any], str, str | None]]] = defaultdict(list)
        for url, meta in entries:
            title = _clean_title(meta.get("name"))
            region = _region(meta.get("region"))
            row = (url, meta, title, region)
            title_id = str(meta.get("title_id") or "").strip().upper()
            if title_id:
                by_id[title_id].append(row)
            if title:
                by_title[title].append(row)
        indexes[key] = {"by_id": by_id, "by_title": by_title}
    return indexes


def _catalog_candidates(
    source: dict[str, Any],
    indexes: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    title_id = str(source.get("title_id") or "").strip().upper()
    title = _clean_title(source.get("title"))
    region = _region(source.get("region"))

    if title_id:
        by_id = list(indexes["by_id"].get(title_id, []))
        if by_id:
            if region:
                regional = [row for row in by_id if row[3] == region]
                if regional:
                    by_id = regional
            scored = sorted(
                ((_title_similarity(title, row[2]), row) for row in by_id),
                key=lambda pair: pair[0],
                reverse=True,
            )
            exact = [row for row in by_id if title and row[2] == title]
            if exact:
                return [(row[0], row[1]) for row in exact]
            plausible = [row for score, row in scored if score >= 0.60]
            if plausible:
                return [(row[0], row[1]) for row in plausible]
            return []

    if not title:
        return []
    matches = indexes["by_title"].get(title, [])
    if region:
        regional = [row for row in matches if row[3] == region]
        if regional:
            matches = regional
        else:
            matches = [row for row in matches if row[3] is None]
    return [(row[0], row[1]) for row in matches]

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


def compare_scan(
    scan: dict[str, Any], catalog_root: Path, catalog_commit: str | None = None
) -> dict[str, Any]:
    if scan.get("scanner") != "mocha":
        raise ValueError("Input is not a Mocha scan report")
    if scan.get("scan_complete") is not True:
        raise ValueError("Refusing catalog comparison against an incomplete Mocha scan")
    if scan.get("catalog_write_performed") is not False:
        raise ValueError("Unexpected scan report write state")

    catalogs = _load_catalogs(catalog_root)
    catalog_indexes = _build_catalog_indexes(catalogs)
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
        candidates = _catalog_candidates(parsed, catalog_indexes[target])
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
            catalog_version = _normalized_version(catalog_metadata.get("version")) or _normalized_version((catalog_filename or {}).get("version"))
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

            source_region = _region(parsed.get("region"))
            catalog_region = _region(catalog_metadata.get("region")) or _region((catalog_filename or {}).get("region"))
            if source_region is None or catalog_region is None:
                unavailable.append("region")
            elif source_region != catalog_region:
                differences.append("region")

            source_min_fw = parsed.get("min_fw")
            catalog_min_fw = catalog_metadata.get("min_fw") or (catalog_filename or {}).get("min_fw")
            if source_min_fw is None or catalog_min_fw is None:
                unavailable.append("min_fw")
            elif str(source_min_fw).strip() != str(catalog_min_fw).strip():
                differences.append("min_fw")

            source_languages = sorted(parsed.get("languages") or [])
            catalog_languages = []
            if catalog_filename:
                catalog_languages = sorted(catalog_filename.get("languages") or [])
            elif isinstance(catalog_metadata.get("languages"), list):
                catalog_languages = sorted(str(x).upper() for x in catalog_metadata["languages"])
            if not source_languages or not catalog_languages:
                unavailable.append("languages")
            elif source_languages != catalog_languages:
                differences.append("languages")

            if parsed.get("title_id"):
                title_similarity = _title_similarity(
                    _clean_title(parsed.get("title")),
                    _clean_title(catalog_metadata.get("name")),
                )
                if title_similarity < 1.0:
                    unavailable.append("package_title_identity")

            if differences:
                status = "metadata_difference"
                reason = "Source metadata differs in: " + ", ".join(differences)
            else:
                # Title ID, minimum firmware, and language tags are useful corroborating
                # fields, but Mocha often omits them. Their absence must not veto a
                # unique exact-title match when the core evidence (region, version,
                # and package size) is present and agrees. Known conflicts above
                # still force human review.
                critical_unavailable = {
                    "version", "size_bytes", "region", "package_title_identity",
                }.intersection(unavailable)
                if critical_unavailable:
                    status = "unverified"
                    reason = (
                        "Catalog identity candidate found, but core verification "
                        "metadata is unavailable: " + ", ".join(sorted(critical_unavailable))
                    )
                else:
                    status = "matched"
                    reason = "Unique catalog identity and all available core metadata agree"
                    if unavailable:
                        reason += "; optional metadata unavailable: " + ", ".join(unavailable)

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
        "catalog_commit": catalog_commit,
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
    parser.add_argument("--catalog-commit", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        scan = json.loads(args.scan_report.read_text(encoding="utf-8"))
        if not isinstance(scan, dict):
            raise ValueError("Mocha scan report must be a JSON object")
        report = compare_scan(scan, args.catalog_root, args.catalog_commit)
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
