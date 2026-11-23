#!/usr/bin/env python3
"""Build read-only Mocha source candidates from exact catalog metadata matches."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from collections import Counter
import re
from pathlib import Path
from typing import Any

from tools.mocha_review_queue import near_duplicate_file_ids
from tools.mocha_catalog_compare import _catalog_filename_metadata, _clean_title, _normalized_version, _region

CATALOGS = {
    ("PS4", "games"): "ps4/games.json",
    ("PS4", "dlc"): "ps4/dlc.json",
    ("PS5", "games"): "ps5/ps5-games.json",
    ("PS5", "dlc"): "ps5/ps5-dlc.json",
}
MOCHA_FILE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _load_catalogs(root: Path) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    catalogs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for key, relative in CATALOGS.items():
        try:
            payload = json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot safely read catalog {relative}: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("DATA"), dict):
            raise ValueError(f"Catalog {relative} does not contain a DATA object")
        entries = payload["DATA"]
        if any(not isinstance(item_key, str) or not isinstance(meta, dict)
               for item_key, meta in entries.items()):
            raise ValueError(f"Catalog {relative} contains malformed entries")
        catalogs[key] = entries
    return catalogs


def catalog_identity_conflict_file_ids(
    rows: list[dict[str, Any]],
    catalog_root: Path,
) -> set[str]:
    """Fail closed when a matched or ambiguous upload touches duplicate/uncertain catalog identity."""
    catalogs = _load_catalogs(catalog_root)
    conflicts: set[str] = set()

    def identity(platform: str, category: str, metadata: dict[str, Any],
                 key: str) -> tuple[Any, ...] | None:
        filename = _catalog_filename_metadata(key)
        title_id = metadata.get("title_id") or (filename or {}).get("title_id")
        title = _clean_title(metadata.get("name") or (filename or {}).get("title"))
        region = _region(metadata.get("region")) or _region((filename or {}).get("region"))
        version = _normalized_version(metadata.get("version")) or _normalized_version((filename or {}).get("version"))
        size = metadata.get("size")
        if not isinstance(size, int) or isinstance(size, bool):
            size = None
        if not title_id or not title or not region or version is None or size is None:
            return None
        return (platform, category, str(title_id).strip().upper(), title,
                region, version, size)

    counts: dict[tuple[Any, ...], int] = {}
    incomplete_by_title_id: set[tuple[str, str, str]] = set()
    for (platform, category), entries in catalogs.items():
        for key, metadata in entries.items():
            ident = identity(platform, category, metadata, key)
            raw_id = metadata.get("title_id")
            if ident is None:
                if isinstance(raw_id, str) and raw_id.strip():
                    incomplete_by_title_id.add((platform, category, raw_id.strip().upper()))
                continue
            counts[ident] = counts.get(ident, 0) + 1

    for row in rows:
        if not isinstance(row, dict):
            continue
        status = row.get("status")
        file_id = row.get("file_id")
        if status == "ambiguous" and isinstance(file_id, str):
            # Comparator has already established multiple catalog candidates.
            conflicts.add(file_id)
            continue
        if status != "matched" or not isinstance(file_id, str):
            continue
        platform, category = row.get("platform"), row.get("category")
        metadata = row.get("catalog_metadata")
        key = row.get("catalog_url")
        if not isinstance(platform, str) or not isinstance(category, str):
            conflicts.add(file_id)
            continue
        if not isinstance(metadata, dict) or not isinstance(key, str):
            conflicts.add(file_id)
            continue
        ident = identity(platform, category, metadata, key)
        if ident is None:
            # A matched row with incomplete identity cannot be proven unique.
            conflicts.add(file_id)
            continue
        title_id_key = (platform, category, ident[2])
        if counts.get(ident, 0) > 1 or title_id_key in incomplete_by_title_id:
            conflicts.add(file_id)
    return conflicts


def build_candidate_queue(
    scan: dict[str, Any],
    comparison: dict[str, Any],
    catalog_root: Path,
    excluded_file_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Propose a Mocha source only for a unique, exact metadata match.

    This function never writes catalog data. Exact, unique candidates are
    eligible for automatic registration; uncertain rows remain in the review queue.
    """
    if scan.get("scanner") != "mocha" or scan.get("scan_complete") is not True:
        raise ValueError("Refusing candidates from an incomplete or invalid Mocha scan")
    if comparison.get("comparison") != "mocha_catalog" or comparison.get("scan_complete") is not True:
        raise ValueError("Refusing candidates from an invalid catalog comparison")
    catalog_commit = comparison.get("catalog_commit")
    if not isinstance(catalog_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", catalog_commit):
        raise ValueError("Comparison does not identify an exact catalog commit")
    scan_observed_at = scan.get("scan_observed_at")
    if not isinstance(scan_observed_at, str) or not scan_observed_at:
        raise ValueError("Scan is missing its observation timestamp")

    catalogs = _load_catalogs(catalog_root)
    existing_mocha_ids = {
        item_key
        for entries in catalogs.values()
        for item_key, metadata in entries.items()
        if metadata.get("download_source") == "mocha"
    }

    scan_files: dict[str, dict[str, Any]] = {}
    for item in scan.get("files", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Scan contains malformed file metadata")
        file_id = item["id"]
        if file_id in scan_files:
            raise ValueError("Scan contains duplicate Mocha file IDs")
        scan_files[file_id] = item

    rows = comparison.get("results")
    if not isinstance(rows, list):
        raise ValueError("Comparison results are missing")
    duplicate_ids = near_duplicate_file_ids(rows)
    duplicate_ids.update(catalog_identity_conflict_file_ids(rows, catalog_root))
    excluded_ids = {str(value) for value in (excluded_file_ids or set())}
    for group in comparison.get("possible_duplicates", []):
        if not isinstance(group, dict) or not isinstance(group.get("file_ids"), list):
            raise ValueError("Comparison contains a malformed duplicate group")
        duplicate_ids.update(str(value) for value in group["file_ids"])

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "matched":
            continue
        file_id = row.get("file_id")
        if not isinstance(file_id, str) or not MOCHA_FILE_ID_RE.fullmatch(file_id):
            continue
        if file_id in seen_ids:
            raise ValueError("Comparison contains duplicate matched file IDs")
        seen_ids.add(file_id)
        if file_id in duplicate_ids or file_id in existing_mocha_ids or file_id in excluded_ids:
            continue

        item = scan_files.get(file_id)
        if item is None:
            raise ValueError("Matched comparison row is absent from the source scan")
        platform, category = row.get("platform"), row.get("category")
        target = (platform, category)
        if target not in CATALOGS or item.get("platform") != platform or item.get("catalog_category") != category:
            raise ValueError("Matched comparison row has an inconsistent catalog target")
        if (item.get("extension") != "pkg" or row.get("name") != item.get("name")
                or not isinstance(item.get("name"), str) or not item["name"].strip()):
            raise ValueError("Matched comparison row does not identify a named scanned PKG")
        created_text = item.get("created_at")
        if not isinstance(created_text, str):
            continue
        try:
            created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created_at.tzinfo is None:
            continue
        if row.get("metadata_differences") != []:
            raise ValueError("Matched comparison row unexpectedly contains metadata differences")

        catalog_key = row.get("catalog_url")
        catalog_metadata = row.get("catalog_metadata")
        entries = catalogs[target]
        if not isinstance(catalog_key, str) or catalog_key not in entries:
            raise ValueError("Matched comparison row references a missing catalog entry")
        if not isinstance(catalog_metadata, dict) or entries[catalog_key] != catalog_metadata:
            raise ValueError("Comparison catalog metadata differs from the checked-out catalog")

        if catalog_metadata.get("download_source") == "mocha":
            continue

        proposed_entry = dict(catalog_metadata)
        proposed_entry["download_source"] = "mocha"
        candidate_id = hashlib.sha256(
            (file_id + "\0" + catalog_commit + "\0" + CATALOGS[target] + "\0" + catalog_key).encode("utf-8")
        ).hexdigest()[:24]
        candidates.append({
            "candidate_id": candidate_id,
            "file_id": file_id,
            "name": item["name"],
            "created_at": created_text,
            "platform": platform,
            "category": category,
            "catalog_path": CATALOGS[target],
            "matched_catalog_key": catalog_key,
            "catalog_metadata": dict(catalog_metadata),
            "proposed_entry": proposed_entry,
            "match_status": "matched",
            "metadata_differences": [],
            "unavailable_metadata": row.get("unavailable_metadata", []),
            "binary_integrity_verified": False,
            "approval_required": False,
        })

    # If multiple Mocha uploads point at the same existing catalog record,
    # neither is safe to register automatically. Keep the whole conflict out.
    target_counts = Counter(
        (candidate["catalog_path"], candidate["matched_catalog_key"])
        for candidate in candidates
    )
    candidates = [
        candidate for candidate in candidates
        if target_counts[(candidate["catalog_path"], candidate["matched_catalog_key"])] == 1
    ]

    return {
        "schema_version": 1,
        "scanner": "mocha",
        "scan_observed_at": scan_observed_at,
        "catalog_commit": catalog_commit,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "catalog_write_performed": False,
        "automatic_approval": True,
        "binary_integrity_verified": False,
    }
