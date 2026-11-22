#!/usr/bin/env python3
"""Validate and apply one scanner-generated Mocha catalog candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

CATALOG_PATHS = {
    ("PS4", "games"): "ps4/games.json",
    ("PS4", "dlc"): "ps4/dlc.json",
    ("PS5", "games"): "ps5/ps5-games.json",
    ("PS5", "dlc"): "ps5/ps5-dlc.json",
}
FILE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
CANDIDATE_ID_RE = re.compile(r"^[0-9a-f]{24}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Cannot safely read {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def apply_candidate(
    queue: dict[str, Any],
    summary: dict[str, Any],
    candidate_id: str,
    repo_root: Path,
    current_commit: str,
) -> tuple[str, str]:
    """Apply one trusted candidate to a working tree after strict snapshot checks."""
    if not CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise ValueError("Invalid candidate ID")
    if queue.get("schema_version") != 1 or queue.get("scanner") != "mocha":
        raise ValueError("Unsupported candidate report")
    if (queue.get("catalog_write_performed") is not False
            or queue.get("automatic_approval") is not False
            or queue.get("binary_integrity_verified") is not False):
        raise ValueError("Candidate report safety flags are invalid")
    if summary.get("schema_version") != 1 or summary.get("scanner") != "mocha" or summary.get("scan_complete") is not True:
        raise ValueError("Latest Mocha scan summary is invalid")
    catalog_commit = queue.get("catalog_commit")
    if (not isinstance(catalog_commit, str) or not COMMIT_RE.fullmatch(catalog_commit)
            or catalog_commit != summary.get("catalog_commit") or catalog_commit != current_commit):
        raise ValueError("The catalog changed after this scan; wait for a fresh scanner run")
    observed_at = queue.get("scan_observed_at")
    if not isinstance(observed_at, str) or not observed_at or observed_at != summary.get("scan_observed_at"):
        raise ValueError("Candidate report is stale or does not match the latest scan summary")
    candidates = queue.get("candidates")
    if (not isinstance(candidates, list) or type(queue.get("candidate_count")) is not int
            or queue["candidate_count"] != len(candidates)):
        raise ValueError("Candidate list/count is malformed")
    matches = [item for item in candidates if isinstance(item, dict) and item.get("candidate_id") == candidate_id]
    if len(matches) != 1:
        raise ValueError("Candidate is missing, stale, or duplicated")
    candidate = matches[0]

    platform, category = candidate.get("platform"), candidate.get("category")
    expected_path = CATALOG_PATHS.get((platform, category))
    if expected_path is None or candidate.get("catalog_path") != expected_path:
        raise ValueError("Candidate catalog target is outside the approved catalog set")
    file_id = candidate.get("file_id")
    if not isinstance(file_id, str) or not FILE_ID_RE.fullmatch(file_id):
        raise ValueError("Candidate contains an invalid Mocha file ID")
    if (candidate.get("match_status") != "matched" or candidate.get("metadata_differences") != []
            or candidate.get("approval_required") is not True
            or candidate.get("binary_integrity_verified") is not False):
        raise ValueError("Only exact metadata matches requiring approval can be processed")
    created_at = candidate.get("created_at")
    if not isinstance(created_at, str):
        raise ValueError("Candidate upload timestamp is missing")
    try:
        parsed_created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Candidate upload timestamp is invalid") from exc
    if parsed_created.tzinfo is None:
        raise ValueError("Candidate upload timestamp must include a timezone")

    path = repo_root / expected_path
    catalog = _read_json(path)
    entries = catalog.get("DATA")
    if not isinstance(entries, dict):
        raise ValueError("Target catalog has no DATA object")
    matched_key = candidate.get("matched_catalog_key")
    matched_metadata = candidate.get("catalog_metadata")
    proposed = candidate.get("proposed_entry")
    if (not isinstance(matched_key, str) or matched_key not in entries
            or not isinstance(matched_metadata, dict) or entries[matched_key] != matched_metadata):
        raise ValueError("The matched catalog entry changed since candidate generation")
    expected_proposal = dict(entries[matched_key])
    expected_proposal["download_source"] = "mocha"
    if proposed != expected_proposal:
        raise ValueError("Candidate proposal does not exactly preserve current catalog metadata")
    if proposed.get("download_source") != "mocha":
        raise ValueError("Candidate does not declare the Mocha source")
    expected_id = hashlib.sha256(
        (file_id + "\0" + catalog_commit + "\0" + expected_path + "\0" + matched_key).encode("utf-8")
    ).hexdigest()[:24]
    if candidate_id != expected_id:
        raise ValueError("Candidate identity does not match the trusted scan/catalog snapshot")

    for relative in CATALOG_PATHS.values():
        existing = _read_json(repo_root / relative).get("DATA")
        if not isinstance(existing, dict):
            raise ValueError(f"{relative} has no valid DATA object")
        if file_id in existing:
            raise ValueError("This Mocha file ID is already present in a catalog")

    entries[file_id] = proposed
    path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    name = candidate.get("name")
    if not isinstance(name, str) or not name.strip():
        name = "Mocha upload"
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = "".join(char for char in name if char.isprintable()).strip()[:120] or "Mocha upload"
    return expected_path, name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--current-commit", required=True)
    args = parser.parse_args()
    queue = _read_json(args.candidate_report)
    summary = _read_json(args.summary)
    path, name = apply_candidate(queue, summary, args.candidate_id, args.repo_root, args.current_commit)
    print(f"catalog_path={path}")
    print(f"candidate_name={name}")


if __name__ == "__main__":
    main()
