#!/usr/bin/env python3
"""Automatically repair only PKG metadata verified against the actual package.

The default mode is a dry run. --apply writes only fields extracted from PARAM.SFO
after the package title ID and exact byte size match the existing catalog record.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import requests

try:
    from . import pkg_remote_metadata as pkg
except ImportError:  # Running as a script from the tools directory.
    import pkg_remote_metadata as pkg


MOCHA_FILE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
VERSION_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")
ALLOWED_REGIONS = {"USA", "EUR", "JAP", "ASIA"}


class RepairError(RuntimeError):
    pass


def _strict_size(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _source_filename(source: str) -> str | None:
    parsed = urlparse(source)
    if not parsed.scheme or not parsed.netloc:
        return None
    filename = unquote(parsed.path.rsplit("/", 1)[-1])
    return filename if filename and filename.casefold().endswith(".pkg") else None


def load_scan_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RepairError(f"Cannot read complete Mocha scan report ({type(exc).__name__})") from exc
    if not isinstance(report, dict) or report.get("scanner") != "mocha":
        raise RepairError("Input is not a Mocha scan report")
    if report.get("scan_complete") is not True:
        raise RepairError("Refusing to repair from an incomplete Mocha inventory")
    if report.get("read_only") is not True or report.get("catalog_write_performed") is not False:
        raise RepairError("Mocha scan report does not prove its read-only state")
    if report.get("package_binaries_downloaded") is not False:
        raise RepairError("Unexpected package binary download state")
    files = report.get("files")
    if not isinstance(files, list):
        raise RepairError("Mocha scan report has no files array")
    if type(report.get("file_count")) is not int or report["file_count"] != len(files):
        raise RepairError("Mocha scan file count does not match its inventory")
    for item in files:
        if not isinstance(item, dict):
            raise RepairError("Mocha scan contains a malformed file entry")
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise RepairError("Mocha scan contains a file without a valid ID")
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise RepairError("Mocha scan contains a file without a name")
        if not isinstance(item.get("folder_path"), str):
            raise RepairError("Mocha scan contains a file without a folder path")
        size = item.get("size_bytes")
        if size is not None and (type(size) is not int or size < 0):
            raise RepairError("Mocha scan contains an invalid file size")
    return report


def find_exact_mocha_matches(target: dict[str, Any], files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find only exact filename + byte-size matches in the matching catalog folder."""
    filename = _source_filename(target["source"])
    expected_size = _strict_size(target["catalog_metadata"].get("size"))
    expected_folder = target["catalog"].removesuffix(".json").strip("/").casefold()
    if not filename or expected_size is None:
        return []
    matches = []
    for item in files:
        if item.get("folder_path", "").strip("/").casefold() != expected_folder:
            continue
        if item.get("extension") != "pkg":
            continue
        if item.get("name", "").casefold() != filename.casefold():
            continue
        if item.get("size_bytes") != expected_size:
            continue
        file_id = item.get("id")
        if not isinstance(file_id, str) or not MOCHA_FILE_ID_RE.fullmatch(file_id):
            continue
        matches.append(item)
    return matches


def _identity_matches(metadata: dict[str, Any], extracted: dict[str, Any]) -> tuple[bool, str | None]:
    expected_title_id = str(metadata.get("title_id") or "").strip().upper()
    actual_title_id = str(extracted.get("title_id") or "").strip().upper()
    if not expected_title_id:
        return False, "Catalog title_id is missing; no independent identity anchor"
    if not actual_title_id:
        return False, "PKG PARAM.SFO did not provide a title_id"
    if expected_title_id != actual_title_id:
        return False, "Extracted title_id does not match the catalog"

    expected_size = _strict_size(metadata.get("size"))
    actual_size = _strict_size(extracted.get("package_size"))
    if expected_size is None:
        return False, "Catalog package size is missing or invalid"
    if actual_size is None:
        return False, "PKG total size could not be verified"
    if expected_size != actual_size:
        return False, "Extracted package size does not match the catalog"
    return True, None


def _safe_updates(target: dict[str, Any], extracted: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    updates: dict[str, str] = {}
    reasons: list[str] = []
    missing = set(target["missing"])
    if "title_id" in missing:
        reasons.append("title_id is missing; automatic repair requires an existing identity anchor")
        return updates, reasons

    if "version" in missing:
        version = extracted.get("version")
        if isinstance(version, str) and VERSION_RE.fullmatch(version.strip()):
            updates["version"] = version.strip()
        else:
            reasons.append("PKG did not provide a valid APP_VER")

    if "region" in missing:
        region = extracted.get("region")
        if isinstance(region, str) and region.strip().upper() in ALLOWED_REGIONS:
            updates["region"] = region.strip().upper()
        else:
            reasons.append("Region is not reliably available in the extracted PKG metadata")

    return updates, reasons


def _redact(source: str) -> str:
    return pkg._redact_source(source)


def inspect_repair_target(
    target: dict[str, Any],
    files: list[dict[str, Any]],
    inspector: Callable[[dict[str, Any]], dict[str, Any]] = pkg.inspect_target,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    original_source = target["source"]
    match_status = "catalog_mocha_id" if MOCHA_FILE_ID_RE.fullmatch(original_source) else "not_found"
    match: dict[str, Any] | None = None
    inspection_source = original_source

    if match_status == "not_found":
        candidates = find_exact_mocha_matches(target, files)
        if len(candidates) == 1:
            match = candidates[0]
            inspection_source = match["id"]
            match_status = "unique_exact_match"
        elif len(candidates) > 1:
            match_status = "ambiguous_exact_matches"

    inspection_target = {**target, "source": inspection_source}
    base = {
        "catalog": target["catalog"],
        "source": _redact(original_source),
        "inspection_source": _redact(inspection_source),
        "platform": target["platform"],
        "missing": list(target["missing"]),
        "mocha_match_status": match_status,
    }
    if match is not None:
        base["mocha_match"] = {
            "file_id": match["id"],
            "name": match["name"],
            "size_bytes": match["size_bytes"],
        }

    try:
        result = inspector(inspection_target)
    except (pkg.MetadataError, requests.RequestException, OSError, ValueError) as exc:
        return ({**base, "status": "unavailable", "reason": str(exc)}, None)

    if result.get("status") != "extracted":
        return ({
            **base,
            "status": result.get("status", "unavailable"),
            "reason": result.get("reason", "PKG metadata extraction did not complete"),
        }, None)

    extracted = {
        key: result.get(key)
        for key in ("title_id", "version", "region", "package_size", "category")
    }
    identity_ok, identity_reason = _identity_matches(target["catalog_metadata"], extracted)
    if not identity_ok:
        return ({
            **base, "status": "identity_mismatch", "reason": identity_reason,
            "extracted": extracted,
        }, None)

    updates, reasons = _safe_updates(target, extracted)
    if not updates:
        return ({
            **base, "status": "extracted_no_safe_update",
            "reason": "; ".join(reasons) if reasons else "No supported missing fields can be repaired",
            "identity_verified": True, "extracted": extracted,
        }, None)

    return ({
        **base,
        "status": "candidate_update",
        "identity_verified": True,
        "extracted": extracted,
        "proposed_updates": updates,
        "unresolved_fields": [field for field in target["missing"] if field not in updates],
        "reason": "; ".join(reasons) if reasons else None,
    }, updates)


def _insert_field(metadata: dict[str, Any], field: str, value: str) -> dict[str, Any]:
    """Preserve existing key order and insert version beside region when possible."""
    updated: dict[str, Any] = {}
    inserted = False
    for key, existing in metadata.items():
        updated[key] = existing
        if field == "version" and key == "region":
            updated[field] = value
            inserted = True
    if not inserted:
        updated[field] = value
    return updated


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise


def repair_catalogs(
    root: Path,
    scan_report: dict[str, Any],
    *,
    apply: bool = False,
    inspector: Callable[[dict[str, Any]], dict[str, Any]] = pkg.inspect_target,
) -> dict[str, Any]:
    files = scan_report["files"]
    targets = pkg.catalog_targets(root)
    documents: dict[str, dict[str, Any]] = {}
    for relative in pkg.CATALOGS:
        path = root / relative
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepairError(f"Cannot safely load {relative} ({type(exc).__name__})") from exc
        if not isinstance(document, dict) or not isinstance(document.get("DATA"), dict):
            raise RepairError(f"{relative} does not contain a DATA object")
        documents[relative] = document

    results: list[dict[str, Any]] = []
    proposed_by_catalog: dict[str, list[dict[str, Any]]] = {}
    for target in targets:
        document = documents.get(target["catalog"])
        current = document["DATA"].get(target["source"]) if document else None
        if not isinstance(current, dict) or current != target["catalog_metadata"]:
            results.append({
                "catalog": target["catalog"], "source": _redact(target["source"]),
                "status": "stale_catalog_entry",
                "reason": "Catalog entry changed while the repair snapshot was being prepared",
            })
            continue

        result, updates = inspect_repair_target(target, files, inspector)
        results.append(result)
        if updates:
            proposed_by_catalog.setdefault(target["catalog"], []).append({
                "source": target["source"],
                "expected_metadata": target["catalog_metadata"],
                "updates": updates,
            })

    proposed_count = sum(len(items) for items in proposed_by_catalog.values())
    applied_count = 0
    if apply and proposed_count:
        replacements: dict[str, dict[str, Any]] = {}
        for relative, items in proposed_by_catalog.items():
            path = root / relative
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RepairError(f"Cannot re-read {relative} before writing ({type(exc).__name__})") from exc
            if not isinstance(document, dict) or not isinstance(document.get("DATA"), dict):
                raise RepairError(f"{relative} changed to an invalid catalog structure")
            for item in items:
                current = document["DATA"].get(item["source"])
                if not isinstance(current, dict) or current != item["expected_metadata"]:
                    raise RepairError(f"Catalog entry changed before applying repair in {relative}")
                for field, value in item["updates"].items():
                    existing = current.get(field)
                    if isinstance(existing, str) and existing.strip():
                        raise RepairError(f"Refusing to overwrite existing {field} in {relative}")
                    if existing not in (None, ""):
                        raise RepairError(f"Refusing to overwrite non-empty {field} in {relative}")
                    document["DATA"][item["source"]] = _insert_field(current, field, value)
                    current = document["DATA"][item["source"]]
                    applied_count += 1
            replacements[relative] = document

        for relative, document in replacements.items():
            _atomic_write_json(root / relative, document)

    for result in results:
        if result.get("status") == "candidate_update":
            result["status"] = "updated" if apply else "candidate_update"

    return {
        "schema_version": 1,
        "scanner": "pkg-metadata-repair",
        "read_only": not apply,
        "scan_observed_at": scan_report.get("scan_observed_at"),
        "targets_found": len(targets),
        "proposed_count": proposed_count,
        "applied_count": applied_count,
        "catalog_files_modified": bool(apply and applied_count),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--apply", action="store_true", help="Apply only verified metadata updates")
    parser.add_argument("--output", type=Path, help="Write JSON report (default: stdout)")
    args = parser.parse_args(argv)
    try:
        scan_report = load_scan_report(args.scan_report)
        report = repair_catalogs(args.catalog_root, scan_report, apply=args.apply)
        output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 0
    except (RepairError, pkg.MetadataError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
