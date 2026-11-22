#!/usr/bin/env python3
"""Apply only scanner-classified, exact Mocha replacements to a working tree."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.approve_mocha_candidate import apply_candidate


def apply_automatic_candidates(
    queue: dict[str, Any],
    summary: dict[str, Any],
    repo_root: Path,
    current_commit: str,
    decisions: dict[str, Any],
    rejections: dict[str, Any],
) -> list[tuple[str, str]]:
    """Apply the complete safe batch or fail closed before publishing anything."""
    candidates = queue.get("candidates")
    if (queue.get("schema_version") != 1 or queue.get("scanner") != "mocha"
            or queue.get("catalog_write_performed") is not False
            or queue.get("automatic_approval") is not False
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

    if summary.get("catalog_commit") != current_commit:
        raise ValueError("The scan is stale; main changed after candidate generation")
    applied: list[tuple[str, str]] = []
    for candidate in candidates:
        applied.append(apply_candidate(
            queue, summary, candidate["candidate_id"], repo_root, current_commit,
            decisions, rejections,
        ))
    return applied


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
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--rejections", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--current-commit", required=True)
    args = parser.parse_args()
    try:
        applied = apply_automatic_candidates(
            _read_json(args.candidate_report),
            _read_json(args.summary),
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
