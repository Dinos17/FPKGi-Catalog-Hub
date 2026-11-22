"""Review-queue classification helpers for Mocha scan reports."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def near_duplicate_file_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Flag uploads sharing core identity, even when optional metadata conflicts.

    Optional tags such as languages and minimum firmware are deliberately not
    part of the grouping key. Both matched and metadata_difference rows matter:
    a conflict on one optional field must not let its matching counterpart pass
    the duplicate check.
    """
    groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    catalog_target_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    eligible_statuses = {"matched", "metadata_difference"}
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in eligible_statuses:
            continue
        source = row.get("source_metadata")
        if not isinstance(source, dict):
            continue
        title = " ".join(str(source.get("title") or "").casefold().split())
        region = str(source.get("region") or "").strip().upper()
        version = str(source.get("version") or "").strip().lstrip("0")
        size = source.get("size_bytes")
        file_id = row.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            continue
        if row.get("status") == "matched":
            catalog_key = row.get("catalog_url")
            if isinstance(catalog_key, str) and catalog_key:
                catalog_target_groups[(row.get("platform"), row.get("category"), catalog_key)].append(file_id)
        if not title or not isinstance(size, int) or isinstance(size, bool) or not version:
            continue
        key = (row.get("platform"), row.get("category"), title, region, version, size)
        groups[key].append(file_id)
    near_duplicates = {file_id for ids in groups.values() if len(ids) > 1 for file_id in ids}
    target_conflicts = {
        file_id for ids in catalog_target_groups.values() if len(ids) > 1 for file_id in ids
    }
    return near_duplicates | target_conflicts


def can_silently_clear_match(
    status: str,
    is_duplicate: bool,
    binary_integrity_verified: Any = None,
) -> bool:
    """Clear unique metadata matches; keep duplicates and nonmatches for review.

    This is a metadata-classification decision, not binary-integrity approval.
    Mocha does not expose a snapshot-consistency guarantee, so that unavailable
    API detail must not disable metadata-based queue clearance.
    """
    return status == "matched" and not is_duplicate
