#!/usr/bin/env python3
"""Apply only scanner-classified, exact Mocha replacements to a working tree."""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.approve_mocha_candidate import apply_candidate
from tools.mocha_catalog_candidates import build_candidate_queue


def apply_automatic_candidates(
    queue: dict[str, Any],
    summary: dict[str, Any],
    scan: dict[str, Any],
    comparison: dict[str, Any],
    repo_root: Path,
    current_commit: str,
    decisions: dict[str, Any],
    rejections: dict[str, Any],
) -> list[tuple[str, str]]:
    """Apply the complete safe batch or fail closed before publishing anything."""
    candidates = queue.get("candidates")
    if (queue.get("schema_version") != 1 or queue.get("scanner") != "mocha"
            or queue.get("catalog_write_performed") is not False
            or queue.get("automatic_approval") is not True
            or queue.get("binary_integrity_verified") is not False
            or not isinstance(candidates, list)
            or type(queue.get("candidate_count")) is not int
            or queue["candidate_count"] != len(candidates)):
        raise ValueError("Candidate report is malformed or has unsafe flags")

    file_ids: set[str] = set()
    catalog_targets: set[tuple[str, str]] = set()
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Candidate report contains a malformed entry")
        file_id = candidate.get("file_id")
        candidate_id = candidate.get("candidate_id")
        target = (candidate.get("catalog_path"), candidate.get("matched_catalog_key"))
        if not isinstance(file_id, str) or file_id in file_ids:
            raise ValueError("Candidate batch contains duplicate or invalid file IDs")
        if not isinstance(candidate_id, str) or candidate_id in candidate_ids:
            raise ValueError("Candidate batch contains duplicate or invalid candidate IDs")
        if not all(isinstance(part, str) and part for part in target) or target in catalog_targets:
            raise ValueError("Candidate batch contains conflicting catalog targets")
        file_ids.add(file_id)
        candidate_ids.add(candidate_id)
        catalog_targets.add(target)

    if (scan.get("scanner") != "mocha" or scan.get("scan_complete") is not True
            or comparison.get("comparison") != "mocha_catalog"
            or comparison.get("scan_complete") is not True
            or summary.get("scanner") != "mocha" or summary.get("scan_complete") is not True
            or scan.get("scan_observed_at") != summary.get("scan_observed_at")
            or comparison.get("catalog_commit") != current_commit
            or summary.get("catalog_commit") != current_commit):
        raise ValueError("The raw scan/comparison snapshot is invalid or stale")

    # Reports-branch JSON is a publication artifact, not an authority. Rebuild
    # the candidate queue from this run's raw scan artifact and current catalog.
    # The published queue must match byte-for-structure before any catalog write.
    excluded_ids = _excluded_ids(scan, comparison, decisions, rejections)
    expected_queue = build_candidate_queue(scan, comparison, repo_root, excluded_ids)
    if expected_queue != queue:
        raise ValueError("Published candidate queue does not match the trusted scan inputs")

    applied: list[tuple[str, str]] = []
    for candidate in candidates:
        applied.append(apply_candidate(
            queue, summary, candidate["candidate_id"], repo_root, current_commit,
            decisions, rejections, automatic=True,
        ))
    return applied


def _normalize_rejection_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Rejection record has no valid filename")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return normalized[:-4] if normalized.endswith(".pkg") else normalized


def _decision_key(platform: Any, category: Any, name: Any, created_text: Any) -> str | None:
    if not all(isinstance(value, str) and value.strip() for value in
               (platform, category, name, created_text)):
        return None
    try:
        uploaded = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if uploaded.tzinfo is None:
        return None
    identity = "\0".join((
        platform, category,
        unicodedata.normalize("NFKC", name).strip().casefold(),
        uploaded.astimezone(timezone.utc).isoformat(),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _excluded_ids(
    scan: dict[str, Any],
    comparison: dict[str, Any],
    decisions: dict[str, Any],
    rejections: dict[str, Any],
) -> set[str]:
    if decisions.get("schema_version") != 1 or not isinstance(decisions.get("records"), list):
        raise ValueError("Saved review decisions are malformed")
    decision_keys: set[str] = set()
    for record in decisions["records"]:
        if (not isinstance(record, dict) or record.get("status") != "resolved"
                or record.get("action") not in {"keep", "reject", "resolve"}
                or not isinstance(record.get("decision_key"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["decision_key"])):
            raise ValueError("Saved review decision record is malformed")
        decision_keys.add(record["decision_key"])

    if rejections.get("schema_version") != 1 or not isinstance(rejections.get("records"), list):
        raise ValueError("Saved rejection report is malformed")
    rejected: dict[tuple[str, str, str], list[datetime]] = {}
    for record in rejections["records"]:
        if not isinstance(record, dict):
            raise ValueError("Saved rejection record is malformed")
        if record.get("status") != "manual_deletion_required":
            continue
        platform, category = record.get("platform"), record.get("catalog_category")
        if platform not in {"PS4", "PS5"} or category not in {"games", "dlc"}:
            raise ValueError("Saved rejection record has an invalid platform/category")
        try:
            rejected_at = datetime.fromisoformat(record["rejected_at"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Saved rejection timestamp is invalid") from exc
        if rejected_at.tzinfo is None:
            raise ValueError("Saved rejection timestamp has no timezone")
        key = (platform, category, _normalize_rejection_name(record.get("name")))
        rejected.setdefault(key, []).append(rejected_at)

    scan_by_id = {
        item.get("id"): item for item in scan.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    excluded: set[str] = set()
    rows = comparison.get("results")
    if not isinstance(rows, list):
        raise ValueError("Raw comparison results are missing")
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "matched":
            continue
        file_id = row.get("file_id")
        item = scan_by_id.get(file_id)
        if not isinstance(file_id, str) or item is None:
            raise ValueError("Matched row is absent from the trusted scan artifact")
        created_text = item.get("created_at")
        rejection_key = (
            row.get("platform"), row.get("category"),
            _normalize_rejection_name(row.get("name")),
        )
        if isinstance(created_text, str):
            try:
                created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
            if created_at is not None and created_at.tzinfo is not None and any(
                    created_at <= rejected_at for rejected_at in rejected.get(rejection_key, [])):
                excluded.add(file_id)
                continue
        key = _decision_key(row.get("platform"), row.get("category"), row.get("name"), created_text)
        if key is not None and key in decision_keys:
            excluded.add(file_id)
    return excluded


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Cannot safely read {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--comparison-report", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--rejections", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--current-commit", required=True)
    args = parser.parse_args()
    try:
        applied = apply_automatic_candidates(
            _read_json(args.candidate_report),
            _read_json(args.summary),
            _read_json(args.scan_report),
            _read_json(args.comparison_report),
            args.repo_root,
            args.current_commit,
            _read_json(args.decisions),
            _read_json(args.rejections),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    print(json.dumps({
        "applied_count": len(applied),
        "catalog_paths": sorted({path for path, _ in applied}),
        "candidate_names": [name for _, name in applied],
        "binary_integrity_verified": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
