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
        if not title or not isinstance(size, int) or isinstance(size, bool) or not version or not file_id:
            continue
        key = (row.get("platform"), row.get("category"), title, region, version, size)
        groups[key].append(str(file_id))
    return {file_id for ids in groups.values() if len(ids) > 1 for file_id in ids}


def can_silently_clear_match(
    status: str,
    is_duplicate: bool,
    binary_integrity_verified: Any = None,
    snapshot_consistency: str = "not_guaranteed_by_api",
) -> bool:
    """Clear only a unique metadata match from a stable, complete inventory.

    This is a metadata-classification decision, not binary-integrity approval.
    A changing or snapshot-inconsistent listing cannot establish uniqueness,
    so such matches stay available for human review.
    """
    return (
        status == "matched"
        and not is_duplicate
        and snapshot_consistency == "stable"
    )
