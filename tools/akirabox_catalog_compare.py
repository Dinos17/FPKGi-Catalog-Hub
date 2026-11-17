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


def parse_source_details(name: str) -> tuple[str, str | None, str | None, str | None]:
    """Parse common scene names and names containing a CUSA/PPSA title ID."""
    stem = re.sub(r"\.(pkg|zip)$", "", name, flags=re.I).strip()
    title_id_match = re.search(r"(?<![A-Z0-9])((?:CUSA|PPSA)\d{5})(?!\d)", stem, re.I)
    title_id = title_id_match.group(1).upper() if title_id_match else None

    region_match = re.search(r"\[\s*(US|USA|EU|EUR|JP|JAP|ASIA)\s*\]", stem, re.I)
    region = REGIONS.get(region_match.group(1).upper()) if region_match else None

    # Prefer explicit _v1.00-style versions so firmware tags such as [5.05]
    # are never mistaken for the package version.
    version_match = re.search(r"(?:^|[_\s-])v(\d+(?:\.\d+)+)(?=$|[_\s-])", stem, re.I)
    if not version_match:
        version_match = re.search(r"\[\s*(\d+(?:\.\d+)+)\s*\]", stem)
    version = version_match.group(1) if version_match else None

    if title_id_match:
        title = stem[:title_id_match.start()]
        title = re.sub(r"^\s*\[[^]]+\]\s*[-_ ]*", "", title)
        title = title.strip(" -_")
    else:
        title = re.sub(r"\s*-?\s*\[[^]]+\]", " ", stem)
    return normalize_title(title), region, version, title_id


def parse_source_identity(name: str) -> tuple[str, str | None, str | None]:
    """Backward-compatible three-field identity parser."""
    title, region, version, _ = parse_source_details(name)
    return title, region, version


def _catalog_identity(metadata: dict[str, Any]) -> tuple[str, str | None]:
    title = normalize_title(metadata.get("name"))
    raw_region = metadata.get("region")
    region = REGIONS.get(str(raw_region).upper()) if raw_region is not None else None
    return title, region


def _title_similarity(left: str, right: str) -> float:
    """Compare package-name tokens, preserving meaningful pack/volume numbers."""
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


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
    title_id_indexes: dict[tuple[str, str], dict[str, list[tuple[str, dict[str, Any]]]]] = {}
    for (platform, category), entries in catalogs.items():
        index: dict[tuple[str, str | None], list[tuple[str, dict[str, Any]]]] = {}
        title_id_index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for url, metadata in entries.items():
            if isinstance(metadata, dict):
                identity = _catalog_identity(metadata)
                if identity[0]:
                    index.setdefault(identity, []).append((url, metadata))
                title_id = metadata.get("title_id")
                if isinstance(title_id, str) and title_id.strip():
                    title_id_index.setdefault(title_id.strip().upper(), []).append((url, metadata))
        indexes[(platform, category)] = index
        title_id_indexes[(platform, category)] = title_id_index

    results = []
    source_seen: dict[tuple[str, str, str, str | None, str | None, str | None], str] = {}
    source_catalog_urls: dict[tuple[str, str], set[str]] = {}
    counts = {"new": 0, "unchanged": 0, "outdated": 0, "duplicate": 0, "unclassified": 0}
    for item in scan.get("files", []):
        platform, category = item.get("platform"), item.get("catalog_category")
        if (platform, category) not in indexes or not isinstance(item.get("name"), str):
            counts["unclassified"] += 1
            results.append({"status": "unclassified", "file_id": item.get("id"),
                            "name": item.get("name"), "reason": "Missing supported platform/category"})
            continue

        title, region, source_version, source_title_id = parse_source_details(item["name"])
        if not title:
            counts["unclassified"] += 1
            results.append({"status": "unclassified", "file_id": item.get("id"),
                            "name": item.get("name"), "reason": "Could not normalize filename"})
            continue
        index = indexes[(platform, category)]
        title_id_index = title_id_indexes[(platform, category)]
        # Different title IDs are different products even when their visible
        # names, regions, and versions happen to be identical.
        identity_key = (platform, category, title, region, source_version, source_title_id)
        if identity_key in source_seen:
            counts["duplicate"] += 1
            results.append({"status": "duplicate", "file_id": item.get("id"), "name": item.get("name"),
                            "duplicate_of_file_id": source_seen[identity_key],
                            "reason": "Duplicate AkiraBox title/region/version already seen in this scan"})
            continue
        source_seen[identity_key] = str(item.get("id"))
        id_candidates = title_id_index.get(source_title_id, []) if source_title_id else []
        exact = index.get((title, region), [])
        # A title ID identifies a base application, not necessarily one DLC
        # package. When several catalog entries share it, disambiguate by the
        # package name instead of treating every sibling as a match.
        ambiguous_title_id = False
        if len(id_candidates) > 1:
            scored = []
            for entry in id_candidates:
                _, metadata = entry
                scored.append((_title_similarity(title, normalize_title(metadata.get("name"))), entry))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if scored and scored[0][0] >= 0.65 and (
                len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08
            ):
                candidates = [scored[0][1]]
            else:
                candidates = []
                ambiguous_title_id = True
        elif id_candidates:
            candidates = id_candidates
        else:
            # If the source filename has no region, only accept a unique title match.
            candidates = exact if region else [
                entry for identity, entries in index.items() if identity[0] == title for entry in entries
            ]
        if len(candidates) == 1:
            source_catalog_urls.setdefault((platform, category), set()).add(candidates[0][0])
        if ambiguous_title_id:
            status, reason = "unclassified", "Title ID is shared by multiple catalog packages and the package name is ambiguous"
        elif len(candidates) > 1:
            status, reason = "duplicate", "Multiple catalog entries match the available title identity"
        elif not candidates:
            status, reason = "new", "No unique matching title ID or title/region in the target catalog"
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
            elif not source_version or not old_version or not isinstance(new_size, int) or not isinstance(old_size, int):
                status, reason = "unclassified", "Matched catalog entry, but version or file-size metadata is missing"
            else:
                status, reason = "unchanged", "Matching title identity; version and file size agree"
        counts[status] += 1
        results.append({
            "status": status, "file_id": item.get("id"), "name": item.get("name"),
            "platform": platform, "category": category, "region": region,
            "version": source_version, "title_id": source_title_id, "share_url": item.get("share_url"), "reason": reason,
        })

    expected = {(platform, category) for platform in ("PS4", "PS5") for category in ("games", "dlc")}
    discovered = set()
    for folder in scan.get("folders", []):
        path = folder.get("path", [])
        if len(path) >= 2 and str(path[1]).casefold() in {"games", "dlc"}:
            discovered.add((str(path[0]).upper(), str(path[1]).casefold()))
    complete = not scan.get("errors") and expected.issubset(discovered)
    missing_candidates = []
    if complete:
        for (platform, category), entries in catalogs.items():
            seen_urls = source_catalog_urls.get((platform, category), set())
            for url, metadata in entries.items():
                if not isinstance(metadata, dict):
                    continue
                if url not in seen_urls:
                    missing_candidates.append({"platform": platform, "category": category,
                                               "name": metadata.get("name"), "region": metadata.get("region"),
                                               "catalog_url": url, "status": "not_seen_in_source"})
    return {
        "scanner": "akirabox_catalog_comparison",
        "read_only": True,
        "catalog_write_performed": False,
        "scan_complete_for_all_categories": complete,
        "missing_check_deferred": not complete,
        "counts": counts,
        "results": results,
        "missing_candidate_count": len(missing_candidates),
        "missing_catalog_entries": missing_candidates,
        "note": ("Missing candidates are only flagged for review; nothing is deleted."
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
