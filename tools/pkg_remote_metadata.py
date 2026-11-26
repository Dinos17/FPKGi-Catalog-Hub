#!/usr/bin/env python3
"""Read PS4 PKG identity metadata with bounded HTTP Range requests.

This tool is read-only: it never downloads a whole package or edits catalog JSON.
PS5 PKG parsing is deliberately not enabled until its format is independently tested.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

RESOLVER_URL = "https://fpkgi-mocha-resolver.netlify.app/presigned"
TIMEOUT = (8, 12)
MAX_HEADER = 0x1000
MAX_ENTRIES = 4096
MAX_ENTRY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 6
MAX_REQUEST_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
PKG_MAGIC = 0x7F434E54
PARAM_SFO_ID = 0x1000
ENTRY_SIZE = 32
CATALOGS = {
    "ps4/dlc.json": "PS4",
    "ps5/ps5-apps.json": "PS5",
}

# Exact public host used by DeepStorage share links; no wildcard subdomains.
DEEPSTORAGE_STORAGE_HOSTS = {"share.deepstorage.dev"}


class MetadataError(RuntimeError):
    pass


def _host_allowed(url: str, extra_hosts: set[str] | None = None) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host or "\\" in parsed.netloc:
            return False
        if host == "archive.org" or host.endswith(".archive.org"):
            return True
        if host == "huggingface.co" or host.endswith(".huggingface.co"):
            return True
        if host == "hf.co" or host.endswith(".hf.co"):
            return True
        return host in (extra_hosts or set())
    except ValueError:
        return False


def _retry_pause(attempt: int) -> None:
    time.sleep(min(0.25 * (2 ** attempt), 1.0))


def _safe_redirect_get(session: requests.Session, url: str, *, headers: dict[str, str],
                       extra_hosts: set[str] | None = None) -> requests.Response:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _host_allowed(current, extra_hosts):
            raise MetadataError("URL or redirect host is not allowlisted")
        response = None
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            try:
                response = session.get(current, headers=headers, timeout=TIMEOUT,
                                       allow_redirects=False, stream=True)
            except requests.RequestException as exc:
                if attempt + 1 == MAX_REQUEST_ATTEMPTS:
                    raise MetadataError(f"HTTP request failed ({type(exc).__name__})") from exc
                _retry_pause(attempt)
                continue
            if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < MAX_REQUEST_ATTEMPTS:
                response.close()
                _retry_pause(attempt)
                continue
            break
        if response is None:
            raise MetadataError("HTTP request failed without a response")
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise MetadataError("Redirect response has no Location")
            current = urljoin(current, location)
            continue
        return response
    raise MetadataError("Too many redirects")


class RangeReader:
    def __init__(self, url: str, *, session: requests.Session | None = None,
                 allowed_hosts: set[str] | None = None):
        self.url = url
        self.session = session or requests.Session()
        self.allowed_hosts = allowed_hosts or set()
        self.total_size: int | None = None

    def read(self, start: int, length: int) -> bytes:
        if start < 0 or length <= 0 or length > MAX_ENTRY_BYTES:
            raise MetadataError("Invalid or oversized byte range")
        end = start + length - 1
        response = _safe_redirect_get(
            self.session, self.url, headers={"Range": f"bytes={start}-{end}",
                                             "Accept-Encoding": "identity",
                                             "User-Agent": "FPKGi-PKG-Metadata/1.0"},
            extra_hosts=self.allowed_hosts,
        )
        try:
            if response.status_code != 206:
                raise MetadataError(f"Server did not honor Range (HTTP {response.status_code})")
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)",
                                 response.headers.get("Content-Range", "").strip())
            if not match:
                raise MetadataError("Missing or invalid Content-Range")
            actual_start, actual_end = int(match.group(1)), int(match.group(2))
            if actual_start != start or actual_end != end:
                raise MetadataError("Server returned a different byte range")
            if match.group(3) != "*":
                total = int(match.group(3))
                if total <= end:
                    raise MetadataError("Content-Range total is smaller than requested range")
                if self.total_size is not None and self.total_size != total:
                    raise MetadataError("Package size changed between range requests")
                self.total_size = total
            data = bytearray()
            for chunk in response.iter_content(chunk_size=min(65536, length)):
                if chunk:
                    data.extend(chunk)
                    if len(data) > length:
                        raise MetadataError("Range response exceeded requested length")
            if len(data) != length:
                raise MetadataError("Range response body length does not match Content-Range")
            return bytes(data)
        finally:
            response.close()


def resolve_mocha_url(file_id: str, *, session: requests.Session | None = None) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", file_id or ""):
        raise MetadataError("Invalid Mocha file ID")
    client = session or requests.Session()
    api_key = os.environ.get("MOCHA_API_KEY", "").strip()
    service = "Mocha API" if api_key else "Mocha resolver"
    response = None
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            if api_key:
                response = client.post(
                    "https://api.mocha.my/api/files/presigned",
                    json={"fileId": file_id, "disposition": "attachment"},
                    timeout=TIMEOUT, allow_redirects=False, stream=True,
                    headers={"Authorization": f"Bearer {api_key}",
                             "Accept": "application/json"},
                )
            else:
                response = client.get(
                    RESOLVER_URL, params={"fileId": file_id},
                    timeout=TIMEOUT, allow_redirects=False, stream=True,
                    headers={"Accept": "application/json"},
                )
        except requests.RequestException as exc:
            if attempt + 1 == MAX_REQUEST_ATTEMPTS:
                raise MetadataError(f"{service} request failed ({type(exc).__name__})") from exc
            _retry_pause(attempt)
            continue
        if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < MAX_REQUEST_ATTEMPTS:
            response.close()
            _retry_pause(attempt)
            continue
        break
    if response is None:
        raise MetadataError(f"{service} request failed without a response")
    try:
        if response.status_code != 200:
            raise MetadataError(f"{service} returned HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=4096):
            if chunk:
                body.extend(chunk)
                if len(body) > 16 * 1024:
                    raise MetadataError("Mocha resolver response is too large")
        payload = json.loads(body)
    except (ValueError, requests.RequestException) as exc:
        raise MetadataError("Mocha resolver returned invalid JSON") from exc
    finally:
        response.close()
    url = payload.get("url") if isinstance(payload, dict) else None
    allowed = {h.strip().lower() for h in os.environ.get(
        "MOCHA_STORAGE_HOSTS", "stor11.mochaprox.li"
    ).split(",") if h.strip()}
    if not isinstance(url, str) or not _host_allowed(url, allowed):
        raise MetadataError("Mocha resolver returned a URL outside the storage host allowlist")
    return url


def parse_pkg_header(header: bytes) -> tuple[int, int, int]:
    if len(header) < 0x20:
        raise MetadataError("PKG header is truncated")
    magic = struct.unpack_from(">I", header, 0)[0]
    if magic != PKG_MAGIC:
        raise MetadataError("Not a recognized PS4 PKG (missing 7F434E54 magic)")
    entry_count = struct.unpack_from(">I", header, 0x10)[0]
    table_offset = struct.unpack_from(">I", header, 0x18)[0]
    if not 0 < entry_count <= MAX_ENTRIES:
        raise MetadataError("PKG entry count is outside safety limits")
    if table_offset < 0x1000 or table_offset > 0xFFFFFFFF:
        raise MetadataError("PKG entry table offset is invalid")
    return entry_count, table_offset, struct.unpack_from(">I", header, 0x0C)[0]


def parse_sfo(data: bytes) -> dict[str, Any]:
    if len(data) < 0x14 or data[:4] != b"\x00PSF":
        raise MetadataError("PARAM.SFO has an invalid header")
    version, key_off, data_off, count = struct.unpack_from("<IIII", data, 4)
    if version not in (0x00000101, 0x00000100) or count > 1024:
        raise MetadataError("PARAM.SFO version or entry count is invalid")
    index_end = 0x14 + count * 0x10
    if index_end > len(data) or key_off < index_end or data_off < key_off:
        raise MetadataError("PARAM.SFO tables are out of bounds")
    if key_off > len(data) or data_off > len(data):
        raise MetadataError("PARAM.SFO table offset exceeds entry")
    result: dict[str, Any] = {}
    for index in range(count):
        key_rel, fmt, value_len, max_len, value_rel = struct.unpack_from(
            "<HHIII", data, 0x14 + index * 0x10
        )
        key_start = key_off + key_rel
        value_start = data_off + value_rel
        if key_start < key_off or key_start >= data_off or value_start < data_off or value_start > len(data) or value_len > max_len or value_len > len(data) - value_start:
            raise MetadataError("PARAM.SFO entry points outside its tables")
        key_end = data.find(b"\0", key_start, data_off)
        if key_end < 0:
            raise MetadataError("PARAM.SFO key is not terminated")
        key = data[key_start:key_end].decode("ascii", errors="strict")
        raw = data[value_start:value_start + value_len]
        if fmt == 0x0404 and value_len == 4:
            value: Any = struct.unpack_from("<I", raw)[0]
        elif fmt == 0x0204:
            value = raw.split(b"\0", 1)[0].decode("utf-8", errors="strict")
        else:
            raise MetadataError(f"Unsupported PARAM.SFO value format 0x{fmt:04X}")
        result[key] = value
    return result


def extract_ps4_metadata(reader: RangeReader) -> dict[str, Any]:
    header = reader.read(0, MAX_HEADER)
    entry_count, table_offset, file_count = parse_pkg_header(header)
    # PS4 PKG header fields documented at offsets 0x20-0x2F and 0x410-0x437.
    body_offset, body_size = struct.unpack_from(">QQ", header, 0x20)
    pfs_image_offset, pfs_image_size = struct.unpack_from(">QQ", header, 0x410)
    declared_pkg_size = struct.unpack_from(">Q", header, 0x430)[0]

    table_length = entry_count * ENTRY_SIZE
    if table_length > MAX_ENTRY_BYTES:
        raise MetadataError("PKG entry table exceeds safety limit")
    table = reader.read(table_offset, table_length)
    sfo_entry = None
    entry_bounds = []
    for offset in range(0, len(table), ENTRY_SIZE):
        entry_id, name_offset, flags1, flags2, entry_offset, entry_size = struct.unpack_from(
            ">IIIIII", table, offset
        )
        if entry_size:
            entry_bounds.append({
                "entry_id": entry_id,
                "offset": entry_offset,
                "size": entry_size,
                "end_offset": entry_offset + entry_size,
            })
        if entry_id == PARAM_SFO_ID:
            if flags1 & 0x80000000:
                raise MetadataError("PARAM.SFO is encrypted; refusing to decrypt or guess")
            if entry_size <= 0 or entry_size > MAX_ENTRY_BYTES:
                raise MetadataError("PARAM.SFO size is outside safety limits")
            sfo_entry = (entry_offset, entry_size)
    if sfo_entry is None:
        raise MetadataError("PKG has no PARAM.SFO entry")

    remote_size = reader.total_size
    bounds_checks = {
        "body_within_remote_file": (
            remote_size is not None and body_offset <= remote_size
            and body_size <= remote_size - body_offset
        ),
        "pfs_image_within_remote_file": (
            remote_size is not None and pfs_image_offset <= remote_size
            and pfs_image_size <= remote_size - pfs_image_offset
        ),
        "entry_table_within_remote_file": (
            remote_size is not None and table_offset + table_length <= remote_size
        ),
        "all_table_entries_within_remote_file": (
            remote_size is not None
            and all(item["offset"] <= remote_size
                    and item["size"] <= remote_size - item["offset"]
                    for item in entry_bounds)
        ),
    }
    declared_size_matches = (
        declared_pkg_size == remote_size if remote_size is not None else None
    )

    sfo_bytes = reader.read(*sfo_entry)
    fields = parse_sfo(sfo_bytes)
    title_id = fields.get("TITLE_ID")
    version = fields.get("APP_VER")
    minimum_fw = fields.get("SYSTEM_VER")
    return {
        "platform": "PS4",
        "title_id": title_id if isinstance(title_id, str) and title_id else None,
        "name": fields.get("TITLE") if isinstance(fields.get("TITLE"), str) else None,
        "version": version if isinstance(version, str) and version else None,
        "min_fw": minimum_fw if isinstance(minimum_fw, str) and minimum_fw else None,
        "category": fields.get("CATEGORY") if isinstance(fields.get("CATEGORY"), str) else None,
        "region": None,
        "region_note": "Not reliably encoded in the fields extracted from PARAM.SFO",
        "package_size": remote_size,
        "pkg_header": {
            "declared_pkg_size": declared_pkg_size,
            "body_offset": body_offset,
            "body_size": body_size,
            "pfs_image_offset": pfs_image_offset,
            "pfs_image_size": pfs_image_size,
            "size_matches_remote": declared_size_matches,
            "bounds_checks": bounds_checks,
            "bounds_valid": all(bounds_checks.values()),
            "entry_count": entry_count,
        },
        "sfo_fields": fields,
        "pkg_file_count": file_count,
        "status": "extracted",
    }


def catalog_targets(root: Path) -> list[dict[str, Any]]:
    targets = []
    for relative, platform in CATALOGS.items():
        path = root / relative
        if not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        entries = document.get("DATA") if isinstance(document, dict) else None
        if not isinstance(entries, dict):
            raise MetadataError(f"{relative} does not contain a DATA object")
        for source, metadata in entries.items():
            if not isinstance(metadata, dict):
                continue
            missing = [key for key in ("title_id", "region", "version")
                       if not isinstance(metadata.get(key), str) or not metadata[key].strip()]
            if missing:
                targets.append({"catalog": relative, "source": source,
                                "platform": platform, "missing": missing,
                                "catalog_metadata": metadata})
    return targets


def _redact_source(source: str) -> str:
    """Remove URL userinfo, query strings, and fragments from report output."""
    parsed = urlparse(source)
    if not parsed.scheme or not parsed.netloc:
        return source
    host = parsed.hostname or ""
    try:
        if parsed.port:
            host = f"{host}:{parsed.port}"
    except ValueError:
        host = parsed.hostname or ""
    return f"{parsed.scheme}://{host}{parsed.path}"


def _report_target_fields(target: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (_redact_source(target[key]) if key == "source" else target[key])
        for key in ("catalog", "source", "platform", "missing")
    }


def inspect_target(target: dict[str, Any], *, session: requests.Session | None = None) -> dict[str, Any]:
    if target["platform"] != "PS4":
        return {**_report_target_fields(target),
                "status": "unsupported_platform",
                "reason": "PS5 PKG format has not yet been independently validated by this extractor"}
    source = target["source"]
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", source):
        url = resolve_mocha_url(source, session=session)
    else:
        url = source
    allowed = {h.strip().lower() for h in os.environ.get(
        "MOCHA_STORAGE_HOSTS", "stor11.mochaprox.li"
    ).split(",") if h.strip()} | DEEPSTORAGE_STORAGE_HOSTS
    reader = RangeReader(url, session=session, allowed_hosts=allowed)
    extracted = extract_ps4_metadata(reader)
    return {**_report_target_fields(target),
            "catalog_metadata": target["catalog_metadata"], **extracted}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--source", default=None, help="Inspect one direct PKG URL or Mocha file ID instead of scanning catalogs")
    parser.add_argument("--platform", choices=("PS4", "PS5"), default="PS4",
                        help="Platform for --source (PS5 remains unsupported until independently validated)")
    parser.add_argument("--output", type=Path, help="Write JSON report (default: stdout)")
    args = parser.parse_args()
    try:
        if args.source is not None:
            if not args.source.strip():
                raise MetadataError("--source must not be empty")
            targets = [{"catalog": "direct-source", "source": args.source,
                        "platform": args.platform, "missing": ["title_id", "version"],
                        "catalog_metadata": {}}]
        else:
            targets = catalog_targets(args.catalog_root)
        results = []
        for target in targets:
            try:
                results.append(inspect_target(target))
            except (MetadataError, requests.RequestException, OSError, ValueError) as exc:
                results.append({**_report_target_fields(target),
                                "catalog_metadata": target["catalog_metadata"],
                                "status": "unavailable", "reason": str(exc)})
        report = {"schema_version": 1, "read_only": True, "targets_found": len(targets),
                  "results": results}
        output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 0
    except (MetadataError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
