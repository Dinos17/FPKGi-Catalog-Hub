#!/usr/bin/env python3
"""Read-only probe for the PS5 FIH/CNT metadata container.

Only bounded HTTP ranges are read. The probe never reads the PFS image, decrypts
encrypted entries, downloads a package binary, or changes catalog files.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from . import pkg_remote_metadata as pkg
except ImportError:
    import pkg_remote_metadata as pkg


FIH_MAGIC = b"\x7FFIH"
CNT_MAGIC = b"\x7FCNT"
FIH_HEADER_READ_SIZE = 0x100
CNT_HEADER_SIZE = 0x5A0
ENTRY_SIZE = 0x20
FIH_SIGNED_BYTE_OFFSET = 0x05
FIH_FORMAT_VERSION_OFFSET = 0x06
FIH_PFS_IMAGE_OFFSET = 0x10
FIH_PFS_IMAGE_SIZE = 0x18
FIH_EMBEDDED_CNT_OFFSET = 0x58
CNT_ENTRY_COUNT_OFFSET = 0x10
CNT_ENTRY_TABLE_OFFSET = 0x18
CNT_CONTENT_ID_OFFSET = 0x40
CNT_CONTENT_ID_SIZE = 0x30
ENTRY_ENCRYPTED_FLAG = 0x80000000
MAX_ENTRY_COUNT = 0x10000
MAX_ENTRY_TABLE_BYTES = 0x200000
MAX_SFO_BYTES = 0x100000


def _safe_reason(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    reason = value.strip()
    lowered = reason.casefold()
    if "://" in reason or "?" in reason or "bearer " in lowered or "signature=" in lowered or "token=" in lowered:
        return "Source request failed; sensitive URL details suppressed"
    return reason[:240]


def _decode_ascii(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("ascii", errors="strict")


def probe_ps5_reader(
    reader: Any,
    *,
    expected_title_id: str | None = None,
    expected_size: int | None = None,
) -> dict[str, Any]:
    # RangeReader learns the object size from Content-Range on its first request.
    prefix = reader.read(0, 4)
    total_size = reader.total_size
    if type(total_size) is not int or total_size <= 0:
        raise pkg.MetadataError("PS5 package source did not provide a valid total size")

    fih_info: dict[str, Any] = {}
    if prefix == FIH_MAGIC:
        fih = reader.read(0, FIH_HEADER_READ_SIZE)
        signed_byte = fih[FIH_SIGNED_BYTE_OFFSET]
        format_version = struct.unpack_from("<H", fih, FIH_FORMAT_VERSION_OFFSET)[0]
        pfs_image_offset = struct.unpack_from("<Q", fih, FIH_PFS_IMAGE_OFFSET)[0]
        pfs_image_size = struct.unpack_from("<Q", fih, FIH_PFS_IMAGE_SIZE)[0]
        cnt_offset = struct.unpack_from("<Q", fih, FIH_EMBEDDED_CNT_OFFSET)[0]
        if cnt_offset < 0x10000 or cnt_offset + CNT_HEADER_SIZE > total_size:
            raise pkg.MetadataError("FIH embedded CNT offset is outside the package")
        fih_info = {
            "fih_signed_byte": signed_byte,
            "fih_format_version": format_version,
            "pfs_image_offset": pfs_image_offset,
            "pfs_image_size": pfs_image_size,
            "embedded_cnt_offset": cnt_offset,
            "cnt_offset_matches_pfs_geometry": cnt_offset == 0x10000 + pfs_image_size,
        }
    elif prefix == CNT_MAGIC:
        cnt_offset = 0
    else:
        raise pkg.MetadataError("Source does not begin with a recognized PS5 FIH or CNT header")

    cnt_header = reader.read(cnt_offset, CNT_HEADER_SIZE)
    if cnt_header[:4] != CNT_MAGIC:
        raise pkg.MetadataError("FIH embedded container does not contain a valid CNT header")

    entry_count = struct.unpack_from(">I", cnt_header, CNT_ENTRY_COUNT_OFFSET)[0]
    entry_table_offset = struct.unpack_from(">I", cnt_header, CNT_ENTRY_TABLE_OFFSET)[0]
    content_id = _decode_ascii(cnt_header[CNT_CONTENT_ID_OFFSET:CNT_CONTENT_ID_OFFSET + CNT_CONTENT_ID_SIZE])
    table_length = entry_count * ENTRY_SIZE
    available_from_cnt = total_size - cnt_offset
    if entry_count <= 0 or entry_count > MAX_ENTRY_COUNT:
        raise pkg.MetadataError("CNT entry count is outside the supported safety limit")
    if table_length > MAX_ENTRY_TABLE_BYTES:
        raise pkg.MetadataError("CNT entry table exceeds the safety limit")
    if entry_table_offset < CNT_HEADER_SIZE or entry_table_offset + table_length > available_from_cnt:
        raise pkg.MetadataError("CNT entry table is outside the package")

    table = reader.read(cnt_offset + entry_table_offset, table_length)
    entries = []
    sfo_entries = []
    for offset in range(0, len(table), ENTRY_SIZE):
        entry_id, name_offset, flags1, flags2, data_offset, data_size = struct.unpack_from(
            ">IIIIII", table, offset
        )
        if data_offset + data_size > available_from_cnt:
            raise pkg.MetadataError("CNT entry data points outside the package")
        entry = {
            "id": entry_id,
            "name_table_offset": name_offset,
            "flags1": flags1,
            "flags2": flags2,
            "data_offset": data_offset,
            "data_size": data_size,
            "encrypted": bool(flags1 & ENTRY_ENCRYPTED_FLAG),
        }
        entries.append(entry)
        if entry_id == pkg.PARAM_SFO_ID:
            sfo_entries.append(entry)

    base = {
        "status": "container_parsed",
        "package_size": total_size,
        **fih_info,
        "cnt_offset": cnt_offset,
        "cnt_entry_count": entry_count,
        "cnt_entry_table_offset": entry_table_offset,
        "cnt_content_id": content_id,
        "cnt_entry_ids": [entry["id"] for entry in entries],
    }

    if len(sfo_entries) == 0:
        return {**base, "status": "param_sfo_missing"}
    if len(sfo_entries) != 1:
        raise pkg.MetadataError("CNT contains multiple PARAM.SFO entries")

    sfo_entry = sfo_entries[0]
    base["param_sfo_entry"] = {
        "data_offset": sfo_entry["data_offset"],
        "data_size": sfo_entry["data_size"],
        "encrypted": sfo_entry["encrypted"],
    }
    if sfo_entry["encrypted"]:
        return {**base, "status": "param_sfo_encrypted"}
    if sfo_entry["data_size"] <= 0 or sfo_entry["data_size"] > MAX_SFO_BYTES:
        raise pkg.MetadataError("PS5 PARAM.SFO size is outside the safety limit")

    sfo_data = reader.read(cnt_offset + sfo_entry["data_offset"], sfo_entry["data_size"])
    fields = pkg.parse_sfo(sfo_data)
    title_id = fields.get("TITLE_ID") if isinstance(fields.get("TITLE_ID"), str) else None
    version = fields.get("APP_VER") if isinstance(fields.get("APP_VER"), str) else None
    min_fw = fields.get("SYSTEM_VER") if isinstance(fields.get("SYSTEM_VER"), str) else None
    category = fields.get("CATEGORY") if isinstance(fields.get("CATEGORY"), str) else None

    identity_errors = []
    if expected_size is not None and total_size != expected_size:
        identity_errors.append("Package byte size does not match the catalog")
    if expected_title_id:
        if not title_id:
            identity_errors.append("PARAM.SFO did not provide TITLE_ID")
        elif title_id.strip().upper() != expected_title_id.strip().upper():
            identity_errors.append("PARAM.SFO TITLE_ID does not match the catalog")

    return {
        **base,
        "status": "identity_mismatch" if identity_errors else "extracted",
        "identity_verified": not identity_errors and bool(expected_title_id) and expected_size is not None,
        "identity_errors": identity_errors,
        "title_id": title_id,
        "version": version,
        "min_fw": min_fw,
        "category": category,
        "region": None,
        "region_note": "Region is not inferred from content ID or language metadata",
    }


def probe_source(
    source: str,
    *,
    expected_title_id: str | None = None,
    expected_size: int | None = None,
) -> dict[str, Any]:
    session = requests.Session()
    try:
        try:
            if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", source or ""):
                resolved_url = pkg.resolve_mocha_url(source, session=session)
                allowed_hosts = {
                    host.strip().lower()
                    for host in os.environ.get("MOCHA_STORAGE_HOSTS", "stor11.mochaprox.li").split(",")
                    if host.strip()
                }
            else:
                resolved_url = source
                allowed_hosts = {"huggingface.co"}
            reader = pkg.RangeReader(
                resolved_url, session=session, allowed_hosts=allowed_hosts
            )
            result = probe_ps5_reader(
                reader,
                expected_title_id=expected_title_id,
                expected_size=expected_size,
            )
            return {
                **result,
                "source": pkg._redact_source(source),
            }
        except (pkg.MetadataError, requests.RequestException, OSError, ValueError, struct.error) as exc:
            return {
                "status": "unavailable",
                "source": pkg._redact_source(source),
                "reason": _safe_reason(str(exc), f"PS5 probe failed ({type(exc).__name__})"),
            }
    finally:
        session.close()


def probe_catalog(root: Path) -> dict[str, Any]:
    targets = [target for target in pkg.catalog_targets(root) if target["platform"] == "PS5"]
    results = []
    for target in targets:
        metadata = target["catalog_metadata"]
        expected_size = metadata.get("size")
        if type(expected_size) is not int or expected_size < 0:
            expected_size = None
        expected_title_id = metadata.get("title_id")
        if not isinstance(expected_title_id, str) or not expected_title_id.strip():
            expected_title_id = None
        result = probe_source(
            target["source"],
            expected_title_id=expected_title_id,
            expected_size=expected_size,
        )
        results.append({
            "catalog": target["catalog"],
            "source": result.get("source"),
            "missing": target["missing"],
            **{key: value for key, value in result.items() if key != "source"},
        })
    return {
        "schema_version": 1,
        "scanner": "ps5-pkg-metadata-probe",
        "read_only": True,
        "package_binaries_downloaded": False,
        "catalog_write_performed": False,
        "targets_found": len(targets),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = probe_catalog(args.catalog_root)
        output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 0
    except (pkg.MetadataError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {_safe_reason(str(exc), type(exc).__name__)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
