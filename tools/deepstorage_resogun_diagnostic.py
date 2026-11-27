#!/usr/bin/env python3
"""Read-only, bounded-range independent PARAM.SFO diagnostic for one fixed PS4 PKG."""
from __future__ import annotations
import json
import re
import struct
import sys
from pathlib import Path
from urllib.parse import urlparse
import requests

SOURCE_URL = (
    "https://share.deepstorage.dev/91be0fdbedb6a37d47530386c9d135e4/"
    "RESOGUN%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.10%5D.pkg"
)
ALLOWED_HOST = "share.deepstorage.dev"
TIMEOUT = (10, 20)
MAX_ENTRIES = 4096
MAX_SFO_SIZE = 1024 * 1024
MAX_TABLE_SIZE = MAX_ENTRIES * 32
MAX_RANGE_SIZE = MAX_SFO_SIZE
PREVIOUS_EXTRACTOR_APP_VER = "01.00"

class DiagnosticError(RuntimeError):
    pass

def validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DiagnosticError("Source URL has an invalid port") from exc
    if (parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST
            or parsed.username or parsed.password or port not in (None, 443)
            or parsed.query or parsed.fragment):
        raise DiagnosticError("Source URL is outside the fixed DeepStorage HTTPS scope")

class BoundedRangeReader:
    """Fetch exact byte ranges only; redirects and full-response fallbacks are refused."""
    def __init__(self, url: str, session: requests.Session | None = None):
        validate_source_url(url)
        self.url = url
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept-Encoding": "identity",
            "User-Agent": "FPKGi-RESOGUN-SFO-Diagnostic/1.0",
        })
        self.total_size: int | None = None
        self.bytes_read = 0
        self.ranges: list[dict[str, int]] = []

    def read(self, start: int, length: int) -> bytes:
        if start < 0 or length <= 0 or length > MAX_RANGE_SIZE:
            raise DiagnosticError("Requested range is outside diagnostic limits")
        end = start + length - 1
        response = None
        try:
            response = self.session.get(
                self.url, headers={"Range": f"bytes={start}-{end}"},
                timeout=TIMEOUT, allow_redirects=False, stream=True,
            )
            if response.status_code != 206:
                raise DiagnosticError(
                    f"DeepStorage did not honor bounded Range request (HTTP {response.status_code})"
                )
            final = urlparse(getattr(response, "url", self.url))
            if (final.scheme != "https" or final.hostname != ALLOWED_HOST
                    or final.username or final.password or final.port not in (None, 443)):
                raise DiagnosticError("Range response URL escaped the approved host")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise DiagnosticError("Compressed Range response is not accepted")
            match = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)",
                response.headers.get("Content-Range", "").strip(),
            )
            if not match:
                raise DiagnosticError("Range response has an invalid Content-Range")
            actual_start, actual_end, total = map(int, match.groups())
            if actual_start != start or actual_end != end or total <= end:
                raise DiagnosticError("Server returned a different or invalid byte range")
            if self.total_size is not None and total != self.total_size:
                raise DiagnosticError("Package size changed between Range requests")
            self.total_size = total
            body = bytearray()
            for chunk in response.iter_content(chunk_size=min(65536, length)):
                if chunk:
                    body.extend(chunk)
                    if len(body) > length:
                        raise DiagnosticError("Range response exceeded requested byte count")
            if len(body) != length:
                raise DiagnosticError("Range response body length did not match requested range")
            self.bytes_read += len(body)
            self.ranges.append({"start": start, "end": end, "bytes": len(body)})
            return bytes(body)
        except requests.RequestException as exc:
            raise DiagnosticError(f"Range request failed ({type(exc).__name__})") from exc
        finally:
            if response is not None:
                response.close()

def parse_pkg_table(header: bytes) -> tuple[int, int]:
    if len(header) != 0x20:
        raise DiagnosticError("PKG header prefix is truncated")
    if struct.unpack_from(">I", header, 0)[0] != 0x7F434E54:
        raise DiagnosticError("Target is not a recognized PS4 PKG")
    entry_count = struct.unpack_from(">I", header, 0x10)[0]
    table_offset = struct.unpack_from(">I", header, 0x18)[0]
    if not 0 < entry_count <= MAX_ENTRIES or table_offset < 0x1000:
        raise DiagnosticError("PKG entry table fields exceed safety limits")
    if entry_count * 32 > MAX_TABLE_SIZE:
        raise DiagnosticError("PKG entry table exceeds safety limit")
    return entry_count, table_offset

def find_sfo_entry(table: bytes, entry_count: int) -> tuple[int, int]:
    if len(table) != entry_count * 32:
        raise DiagnosticError("PKG entry table is truncated")
    found: tuple[int, int] | None = None
    for index in range(entry_count):
        base = index * 32
        entry_id, _name_offset, flags1, _flags2, data_offset, data_size = struct.unpack_from(
            ">IIIIII8x", table, base
        )
        if entry_id == 0x1000:
            if flags1 & 0x80000000:
                raise DiagnosticError("PARAM.SFO is encrypted; refusing to decrypt or guess")
            if found is not None:
                raise DiagnosticError("PKG contains duplicate PARAM.SFO table entries")
            found = (data_offset, data_size)
    if found is None:
        raise DiagnosticError("PKG entry table has no PARAM.SFO (0x1000) entry")
    offset, size = found
    if not 0 < size <= MAX_SFO_SIZE:
        raise DiagnosticError("PARAM.SFO size is outside the 1 MiB diagnostic limit")
    return offset, size

def parse_sfo_independent(data: bytes) -> dict[str, str | int]:
    """Decode PSF fields via explicit byte slices, independently of the catalog parser."""
    if len(data) < 0x14 or data[:4] != b"\x00PSF":
        raise DiagnosticError("PARAM.SFO magic/header is invalid")
    version, key_base, value_base, count = struct.unpack_from("<IIII", data, 4)
    if version not in (0x00000100, 0x00000101) or count > 1024:
        raise DiagnosticError("PARAM.SFO version or index count is unsupported")
    index_base = 0x14
    index_limit = index_base + count * 16
    if (index_limit > len(data) or key_base < index_limit
            or key_base > value_base or value_base > len(data)):
        raise DiagnosticError("PARAM.SFO key/value tables are out of bounds")
    fields: dict[str, str | int] = {}
    for index in range(count):
        pos = index_base + index * 16
        key_rel = struct.unpack_from("<H", data, pos)[0]
        format_code = struct.unpack_from("<H", data, pos + 2)[0]
        used_length, max_length, value_rel = struct.unpack_from("<III", data, pos + 4)
        key_pos = key_base + key_rel
        value_pos = value_base + value_rel
        if (key_pos < key_base or key_pos >= value_base or value_pos < value_base
                or value_pos > len(data) or used_length > max_length
                or used_length > len(data) - value_pos):
            raise DiagnosticError("PARAM.SFO index points outside its tables")
        key_end = data.find(b"\0", key_pos, value_base)
        if key_end < 0:
            raise DiagnosticError("PARAM.SFO key is not NUL-terminated")
        key = data[key_pos:key_end].decode("ascii", errors="strict")
        raw_value = data[value_pos:value_pos + used_length]
        if format_code == 0x0204:
            value: str | int = raw_value.split(b"\0", 1)[0].decode("utf-8", errors="strict")
        elif format_code == 0x0404 and used_length == 4:
            value = struct.unpack("<I", raw_value)[0]
        else:
            continue
        fields[key] = value
    return fields

def extract_diagnostic(reader: BoundedRangeReader) -> dict[str, object]:
    header = reader.read(0, 0x20)
    entry_count, table_offset = parse_pkg_table(header)
    table = reader.read(table_offset, entry_count * 32)
    sfo_offset, sfo_size = find_sfo_entry(table, entry_count)
    if reader.total_size is None or sfo_offset + sfo_size > reader.total_size:
        raise DiagnosticError("PARAM.SFO byte range exceeds reported package size")
    fields = parse_sfo_independent(reader.read(sfo_offset, sfo_size))
    app_ver = fields.get("APP_VER")
    if not isinstance(app_ver, str) or not re.fullmatch(r"\d{2}\.\d{2}", app_ver):
        raise DiagnosticError("Independent PARAM.SFO parse did not produce a valid APP_VER string")
    return {
        "package": "RESOGUN - [US] [EN] [1.10].pkg",
        "source_url": SOURCE_URL,
        "package_size_bytes": reader.total_size,
        "pkg_entry_count": entry_count,
        "param_sfo_offset": sfo_offset,
        "param_sfo_size_bytes": sfo_size,
        "title": fields.get("TITLE"),
        "title_id": fields.get("TITLE_ID"),
        "app_ver": app_ver,
        "version": fields.get("VERSION"),
        "system_ver": fields.get("SYSTEM_VER"),
        "category": fields.get("CATEGORY"),
        "previous_catalog_sync_extractor_app_ver": PREVIOUS_EXTRACTOR_APP_VER,
        "matches_previous_extractor": app_ver == PREVIOUS_EXTRACTOR_APP_VER,
        "bytes_requested": reader.bytes_read,
        "ranges_requested": reader.ranges,
        "package_binary_downloaded": False,
        "catalog_modified": False,
    }

def main() -> int:
    output_path = Path("reports/deepstorage-resogun-diagnostic.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "schema_version": 1,
        "diagnostic": "independent-resogun-param-sfo",
        "read_only": True,
        "package_binary_downloaded": False,
        "catalog_modified": False,
        "source_url": SOURCE_URL,
    }
    try:
        reader = BoundedRangeReader(SOURCE_URL)
        report["result"] = extract_diagnostic(reader)
        report["status"] = "success"
    except (DiagnosticError, requests.RequestException, UnicodeError, struct.error) as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "success" else 1

if __name__ == "__main__":
    raise SystemExit(main())
