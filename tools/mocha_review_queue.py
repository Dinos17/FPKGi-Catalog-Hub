"""Conservative review-queue safety helpers for Mocha scan reports."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def near_duplicate_file_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Flag matched uploads sharing core identity while optional tags may differ."""
    groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "matched":
            continue
        source = row.get("source_metadata")
        if not isinstance(source, dict):
            continue
        title = " ".join(str(source.get("title") or "").casefold().split())
        region = str(source.get("region") or "").strip().upper()
        version = str(source.get("version") or "").strip().lstrip("0")
        size = source.get("size_bytes")
        file_id = row.get("file_id")
        if not title or not isinstance(size, int) or isinstance(size, bool) or not version or not file_id:
            continue
        key = (row.get("platform"), row.get("category"), title, region, version, size)
        groups[key].append(str(file_id))
    return {file_id for ids in groups.values() if len(ids) > 1 for file_id in ids}


def can_silently_clear_match(
    status: str, is_duplicate: bool, binary_integrity_verified: Any
) -> bool:
    """Only cryptographically verified, unique matches may leave human review."""
    return status == "matched" and not is_duplicate and binary_integrity_verified is True
