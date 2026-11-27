#!/usr/bin/env python3
"""Prepare guarded DeepStorage catalog additions and source-URL updates.

Only complete PS4 scans are accepted. Every candidate is checked through bounded
HTTP Range reads against the exact DeepStorage host. Changes are written to the
checked-out working tree so GitHub Actions can propose them in a pull request.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import requests

try:
    from .akirabox_catalog_compare import normalize_title, parse_source_metadata
    from .merge import validate_entry
    from .pkg_remote_metadata import MetadataError, extract_ps4_metadata
except ImportError:  # Running as a script from the tools directory.
    from akirabox_catalog_compare import normalize_title, parse_source_metadata
    from merge import validate_entry
    from pkg_remote_metadata import MetadataError, extract_ps4_metadata

HOST = "share.deepstorage.dev"
CATALOGS = {
    "games": Path("ps4/games.json"),
    "dlc": Path("ps4/dlc.json"),
}
TIMEOUT = (10, 20)
MAX_REDIRECTS = 6
MAX_RANGE_BYTES = 2 * 1024 * 1024


def safe_deepstorage_url(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("missing DeepStorage URL")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("malformed DeepStorage URL port") from exc
    if (parsed.scheme != "https" or parsed.hostname != HOST
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.query or parsed.fragment
            or not parsed.path.lower().endswith(".pkg")):
        raise ValueError("URL is not an approved DeepStorage .pkg URL")
    return value


class DeepStorageRangeReader:
    """HTTP Range reader that never permits redirects away from DeepStorage."""

    def __init__(self, url: str, session: requests.Session | None = None):
        self.url = safe_deepstorage_url(url)
        self.session = session or requests.Session()
        self.total_size: int | None = None

    def read(self, start: int, length: int) -> bytes:
        if start < 0 or length <= 0 or length > MAX_RANGE_BYTES:
            raise ValueError("invalid or oversized PKG byte range")
        current = self.url
        end = start + length - 1
        response = None
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlsplit(current)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("malformed redirect port") from exc
            if (parsed.scheme != "https" or parsed.hostname != HOST
                    or parsed.username is not None or parsed.password is not None
                    or port not in (None, 443)):
                raise ValueError("DeepStorage redirect left the exact approved HTTPS host")
            response = self.session.get(
                current,
                headers={
                    "Range": f"bytes={start}-{end}",
                    "Accept-Encoding": "identity",
                    "User-Agent": "FPKGi-DeepStorage-Catalog-Sync/1.0",
                },
                timeout=TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                break
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("DeepStorage redirect has no Location header")
            current = urljoin(current, location)
        else:
            raise ValueError("too many DeepStorage redirects")

        try:
            if response is None or response.status_code != 206:
                status = response.status_code if response is not None else "no response"
                raise ValueError(f"DeepStorage did not honor Range (HTTP {status})")
            match = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)",
                response.headers.get("Content-Range", "").strip(),
            )
            if not match:
                raise ValueError("missing or invalid Content-Range")
            actual_start, actual_end, total = map(int, match.groups())
            if actual_start != start or actual_end != end or total <= end:
                raise ValueError("DeepStorage returned an unexpected byte range")
            if self.total_size is not None and self.total_size != total:
                raise ValueError("DeepStorage package size changed between Range requests")
            self.total_size = total
            data = bytearray()
            for chunk in response.iter_content(chunk_size=min(65536, length)):
                if chunk:
                    data.extend(chunk)
                    if len(data) > length:
                        raise ValueError("Range response exceeded requested length")
            if len(data) != length:
                raise ValueError("Range response length did not match Content-Range")
            return bytes(data)
        finally:
            if response is not None:
                response.close()


def _category(folder_path: Any, scan_folder: str) -> str | None:
    for value in (folder_path, scan_folder):
        if not isinstance(value, str):
            continue
        parts = [part.casefold() for part in value.split("/") if part]
        if len(parts) >= 2 and parts[0] == "ps4" and parts[1] in CATALOGS:
            return parts[1]
    return None


def _normalized_version(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value).strip().lstrip("0") or "0"


def inspect_file(item: dict[str, Any], scan_folder: str) -> dict[str, Any]:
    name = item.get("name")
    if not isinstance(name, str) or not name.lower().endswith(".pkg"):
        raise ValueError("inventory row is not a named .pkg file")
    url = safe_deepstorage_url(item.get("download_url"))
    category = _category(item.get("folder_path"), scan_folder)
    if category is None:
        raise ValueError("only PS4/Games and PS4/DLC folders are supported")
    listed_size = item.get("size_bytes")
    if isinstance(listed_size, bool) or not isinstance(listed_size, int) or listed_size <= 0:
        raise ValueError("listing size is invalid")

    filename = parse_source_metadata(name)
    reader = DeepStorageRangeReader(url)
    extracted = extract_ps4_metadata(reader)
    header = extracted.get("pkg_header")
    if not isinstance(header, dict) or header.get("bounds_valid") is not True:
        raise ValueError("PKG structural bounds are invalid; registration blocked")
    if header.get("size_matches_remote") is not True:
        raise ValueError("PKG header declared size does not match the remote package size")
    if extracted.get("package_size") != listed_size or reader.total_size != listed_size:
        raise ValueError("DeepStorage listing size and Range-reported package size disagree")

    title_id = extracted.get("title_id")
    version = extracted.get("version")
    raw_name = extracted.get("name")
    region = filename.get("region")
    if not isinstance(title_id, str) or not re.fullmatch(r"[A-Z]{4}\d{5}", title_id, re.I):
        raise ValueError("PARAM.SFO did not provide a valid title ID")
    if filename.get("title_id") and filename["title_id"].upper() != title_id.upper():
        raise ValueError("filename title ID conflicts with PARAM.SFO")
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ValueError("PARAM.SFO did not provide a valid package version")
    if filename.get("version") and _normalized_version(filename["version"]) != _normalized_version(version):
        raise ValueError(
            f"filename version {filename['version']} conflicts with "
            f"PARAM.SFO APP_VER {version}"
        )
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("PARAM.SFO did not provide a package title")
    if not isinstance(region, str) or region not in {"USA", "EUR", "JAP", "ASIA"}:
        raise ValueError("region is missing or cannot be established from the filename")

    return {
        "url": url,
        "name": name,
        "category": category,
        "title_id": title_id.upper(),
        "title": raw_name.strip(),
        "normalized_title": normalize_title(raw_name),
        "region": region,
        "version": version,
        "size": listed_size,
        "min_fw": extracted.get("min_fw"),
        "pkg_header": header,
    }


def _entry_identity(key: str, metadata: dict[str, Any]) -> tuple[str, str, str, str] | None:
    title_id = metadata.get("title_id")
    title = metadata.get("name")
    region = metadata.get("region")
    version = metadata.get("version")
    if not all(isinstance(value, str) and value.strip()
               for value in (title_id, title, region, version)):
        filename = parse_source_metadata(unquote(key.rsplit("/", 1)[-1]))
        title_id = title_id or filename.get("title_id")
        title = title or metadata.get("name") or filename.get("title")
        region = region or filename.get("region")
        version = version or filename.get("version")
    if not all(isinstance(value, str) and value.strip()
               for value in (title_id, title, region, version)):
        return None
    return (
        title_id.strip().upper(),
        normalize_title(title),
        region.strip().upper(),
        _normalized_version(version) or "",
    )


def _source_metadata(record: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "title_id": record["title_id"],
        "region": record["region"],
        "name": record["title"],
        "version": record["version"],
        "size": record["size"],
        "download_source": "deepstorage",
    }
    if isinstance(record.get("min_fw"), str) and record["min_fw"].strip():
        metadata["min_fw"] = record["min_fw"].strip()
    return metadata


def sync_catalog(scan: dict[str, Any], catalog_root: Path) -> dict[str, Any]:
    if scan.get("scanner") != "deepstorage" or scan.get("scan_complete") is not True:
        raise ValueError("refusing to sync an incomplete or invalid DeepStorage scan")
    if scan.get("read_only") is not True or scan.get("catalog_write_performed") is not False:
        raise ValueError("scan safety flags are invalid")
    files = scan.get("files")
    if not isinstance(files, list):
        raise ValueError("scan report has no files array")
    scan_folder = scan.get("folder_path")
    if not isinstance(scan_folder, str):
        raise ValueError("scan folder path is missing")

    catalogs: dict[str, dict[str, Any]] = {}
    for category, relative in CATALOGS.items():
        path = catalog_root / relative
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"cannot safely read {relative}: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("DATA"), dict):
            raise ValueError(f"{relative} has no valid DATA object")
        catalogs[category] = payload

    inspected: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("scan contains a malformed file row")
        if item.get("extension") != "pkg":
            continue
        item_folder = item.get("folder_path")
        folder_parts = [part.casefold() for part in item_folder.split("/") if part] if isinstance(item_folder, str) else []
        if folder_parts and folder_parts[0] == "ps5":
            results.append({
                "status": "unsupported",
                "name": item.get("name"),
                "url": item.get("download_url"),
                "reason": "PS5 is inventory-only; catalog registration is disabled until PS5 PKG metadata extraction is independently validated",
            })
            continue
        try:
            inspected.append(inspect_file(item, scan_folder))
        except (MetadataError, ValueError, requests.RequestException, OSError) as exc:
            results.append({
                "status": "blocked",
                "name": item.get("name"),
                "url": item.get("download_url"),
                "reason": str(exc),
            })

    # Two scanned uploads with the same complete identity are never auto-selected.
    identity_counts = Counter(
        (record["category"], record["title_id"], record["normalized_title"],
         record["region"], _normalized_version(record["version"]))
        for record in inspected
    )
    for record in inspected:
        identity = (
            record["category"], record["title_id"], record["normalized_title"],
            record["region"], _normalized_version(record["version"]),
        )
        if identity_counts[identity] > 1:
            results.append({
                "status": "ambiguous",
                "name": record["name"],
                "url": record["url"],
                "reason": "multiple DeepStorage files have the same verified package identity",
            })
            continue

        entries = catalogs[record["category"]]["DATA"]
        exact_identity = (
            record["title_id"], record["normalized_title"], record["region"],
            _normalized_version(record["version"]),
        )
        candidates = [
            (key, metadata) for key, metadata in entries.items()
            if _entry_identity(key, metadata) == exact_identity
        ]
        related = [
            (key, metadata) for key, metadata in entries.items()
            if isinstance(metadata.get("title_id"), str)
            and metadata["title_id"].strip().upper() == record["title_id"]
            and _normalized_version(metadata.get("version")) == _normalized_version(record["version"])
            and (not metadata.get("region") or str(metadata.get("region", "")).strip().upper() == record["region"])
        ]
        if len(candidates) > 1 or (not candidates and related):
            results.append({
                "status": "ambiguous",
                "name": record["name"],
                "url": record["url"],
                "reason": "catalog contains multiple or conflicting records for this package identity",
            })
            continue

        new_metadata = _source_metadata(record)
        if candidates:
            old_key, old_metadata = candidates[0]
            if old_metadata.get("download_source") == "mocha":
                results.append({
                    "status": "protected",
                    "name": record["name"],
                    "url": record["url"],
                    "catalog_url": old_key,
                    "reason": "existing Mocha registration is protected from automatic replacement",
                })
                continue
            old_size = old_metadata.get("size")
            if isinstance(old_size, int) and old_size != record["size"]:
                results.append({
                    "status": "blocked",
                    "name": record["name"],
                    "url": record["url"],
                    "catalog_url": old_key,
                    "reason": "verified DeepStorage size conflicts with the existing catalog size",
                })
                continue
            for field in ("title_id", "version", "region"):
                current = old_metadata.get(field)
                proposed = new_metadata.get(field)
                if current not in (None, "") and str(current).strip().upper() != str(proposed).strip().upper():
                    raise ValueError(f"refusing conflicting {field} while updating {old_key}")
            if old_key != record["url"] and record["url"] in entries:
                results.append({
                    "status": "blocked",
                    "name": record["name"],
                    "url": record["url"],
                    "catalog_url": old_key,
                    "reason": "DeepStorage URL already exists as a separate catalog key",
                })
                continue
            merged = dict(old_metadata)
            for field, value in new_metadata.items():
                if field == "download_source" or merged.get(field) in (None, ""):
                    merged[field] = value
            errors, _ = validate_entry(record["url"], merged, allow_deepstorage=True)
            if errors:
                raise ValueError("catalog validator rejected update: " + "; ".join(errors))
            if old_key == record["url"] and merged == old_metadata:
                results.append({
                    "status": "unchanged",
                    "name": record["name"],
                    "url": record["url"],
                    "catalog_url": old_key,
                    "reason": "catalog already points to this verified DeepStorage package",
                })
                continue
            if old_key != record["url"]:
                del entries[old_key]
            entries[record["url"]] = merged
            results.append({
                "status": "updated",
                "name": record["name"],
                "url": record["url"],
                "previous_catalog_url": old_key,
                "reason": "replaced the uniquely matched source URL with the verified DeepStorage URL",
            })
        else:
            errors, _ = validate_entry(record["url"], new_metadata, allow_deepstorage=True)
            if errors:
                raise ValueError("catalog validator rejected new entry: " + "; ".join(errors))
            if record["url"] in entries:
                results.append({
                    "status": "blocked",
                    "name": record["name"],
                    "url": record["url"],
                    "reason": "DeepStorage URL already exists with different metadata",
                })
                continue
            entries[record["url"]] = new_metadata
            results.append({
                "status": "added",
                "name": record["name"],
                "url": record["url"],
                "reason": "no existing catalog identity matched; added verified PS4 package",
            })

    changed_paths = []
    for category, relative in CATALOGS.items():
        path = catalog_root / relative
        original = json.loads(path.read_text(encoding="utf-8"))
        if catalogs[category] != original:
            path.write_text(
                json.dumps(catalogs[category], indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            changed_paths.append(str(relative))

    counts = Counter(row["status"] for row in results)
    return {
        "schema_version": 1,
        "scanner": "deepstorage",
        "scan_complete": True,
        "catalog_write_performed": bool(changed_paths),
        "changed_catalogs": changed_paths,
        "counts": dict(counts),
        "results": results,
        "package_binaries_downloaded": False,
        "binary_integrity_verified": False,
        "notes": [
            "PS4 Games and DLC are eligible for catalog changes; PS5 is inventory-only",
            "Only bounded Range requests to share.deepstorage.dev are permitted",
            "New entries omit release date and cover URL when those fields cannot be established",
            "Mocha registrations are never replaced automatically",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        scan = json.loads(args.scan_report.read_text(encoding="utf-8"))
        if not isinstance(scan, dict):
            raise ValueError("DeepStorage scan report must be a JSON object")
        report = sync_catalog(scan, args.catalog_root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    print(f"Changed catalogs: {', '.join(report['changed_catalogs']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
