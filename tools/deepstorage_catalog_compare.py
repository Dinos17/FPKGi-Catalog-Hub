#!/usr/bin/env python3
"""Compare a DeepStorage PKG listing with the FPKGi catalogs (read-only).

This first comparison stage reuses Mocha's established catalog identity and
metadata comparison rules. It compares filename-exposed metadata and listing
file sizes only; it does not fetch package bytes or claim binary verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.mocha_catalog_compare import compare_scan


ALLOWED_HOST = "share.deepstorage.dev"


def _category(path: Any, fallback: str) -> tuple[str | None, str | None]:
    candidates = [path, fallback]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        parts = [part.strip().casefold() for part in candidate.split("/") if part.strip()]
        for index, part in enumerate(parts):
            if part in {"ps4", "ps5"}:
                for child in parts[index + 1:]:
                    if child in {"games", "dlc"}:
                        return part.upper(), child
                return part.upper(), None
    return None, None


def _safe_source_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("DeepStorage scan contains a missing source URL")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("DeepStorage scan contains a source URL outside the approved host")
    if not parsed.path.startswith("/") or not parsed.path.strip("/"):
        raise ValueError("DeepStorage scan contains an invalid source URL path")
    return value


def compare_deepstorage(
    scan: dict[str, Any], catalog_root: Path, catalog_commit: str | None = None
) -> dict[str, Any]:
    if scan.get("scanner") != "deepstorage" or scan.get("scan_complete") is not True:
        raise ValueError("Refusing catalog comparison against an incomplete or invalid DeepStorage scan")
    if scan.get("catalog_write_performed") is not False:
        raise ValueError("Unexpected DeepStorage scan write state")
    files = scan.get("files")
    if not isinstance(files, list):
        raise ValueError("DeepStorage scan is missing its files array")

    synthetic_files: list[dict[str, Any]] = []
    source_urls: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("DeepStorage scan contains a malformed file row")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("DeepStorage scan contains a file without a valid name")
        source_url = _safe_source_url(item.get("download_url"))
        source_key = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        if source_key in source_urls:
            continue
        source_urls[source_key] = source_url
        platform, category = _category(item.get("folder_path"), scan.get("folder_path", ""))
        size = item.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            size = None
        synthetic_files.append({
            "id": source_key,
            "name": name,
            "size_bytes": size,
            "folder_path": item.get("folder_path") if isinstance(item.get("folder_path"), str) else scan.get("folder_path"),
            "extension": item.get("extension"),
            "platform": platform,
            "catalog_category": category,
        })

    # The shared comparator has a Mocha-specific input contract. Adapt only
    # the in-memory shape; no Mocha API, credentials, or Netlify services are used.
    adapted_scan = {
        "scanner": "mocha",
        "scan_complete": True,
        "catalog_write_performed": False,
        "snapshot_consistency": scan.get("snapshot_consistency"),
        "permissions": {},
        "files": synthetic_files,
    }
    report = compare_scan(adapted_scan, catalog_root, catalog_commit)
    report["comparison"] = "deepstorage_catalog"
    report["scanner"] = "deepstorage"
    report["source_username"] = scan.get("username")
    report["source_folder"] = scan.get("folder_path")
    report["scan_observed_at"] = scan.get("scan_observed_at")
    report["source_file_count"] = len(files)
    report["catalog_write_performed"] = False
    report["package_binaries_downloaded"] = False
    report["binary_integrity_verified"] = False
    report["comparison_basis"] = [
        "filename-exposed title, title ID, region, version, language and firmware tags",
        "DeepStorage listing file size",
    ]
    report["limitations"] = [
        "PKG binaries were not downloaded",
        "PKG internal PARAM.SFO metadata was not extracted in this stage",
        "Metadata agreement is not cryptographic or binary verification",
        "Missing catalog entries are not inferred from a partial folder scan",
    ]

    for row in report.get("results", []):
        if isinstance(row, dict):
            key = row.get("file_id")
            row["source_key"] = key
            row["deepstorage_url"] = source_urls.get(key)
            row.pop("file_id", None)

    for group in report.get("possible_duplicates", []):
        if isinstance(group, dict) and isinstance(group.get("file_ids"), list):
            group["source_keys"] = group.pop("file_ids")
            group["deepstorage_urls"] = [
                source_urls[key] for key in group["source_keys"] if key in source_urls
            ]

    return report


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
            raise ValueError("DeepStorage scan report must be a JSON object")
        report = compare_deepstorage(scan, args.catalog_root, args.catalog_commit)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("DeepStorage catalog comparison complete.")
    print(json.dumps(report["counts"], sort_keys=True))
    print(f"Possible duplicate groups: {len(report['possible_duplicates'])} (not auto-cleaned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
