import struct
from typing import Dict, Optional

import requests


TIMEOUT = 60
PKG_MAGIC = 0x7F434E54
PARAM_SFO_ID = 0x1000
REGION_MAP = {
    "EP": "EUR",
    "UP": "USA",
    "JP": "JPN",
    "HP": "HKG",
    "KP": "KOR",
    "TP": "USA",
    "WG": "CHN",
}


class RangeReader:
    def __init__(self, url: str, size: int):
        self.url = url
        self.size = size

    def read(self, start: int, length: int) -> bytes:
        if start < 0 or length < 0 or start + length > self.size:
            raise ValueError("Requested byte range is outside the asset")

        end = start + length - 1
        response = requests.get(
            self.url,
            headers={
                "Range": f"bytes={start}-{end}",
                "Accept-Encoding": "identity",
                "User-Agent": "FPKGi-Catalog-Hub/1.0",
            },
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()

        content = response.content
        content_range = response.headers.get("Content-Range", "")

        if response.status_code != 206 or not content_range.startswith("bytes "):
            raise RuntimeError(
                "PKG source did not honor HTTP range requests; refusing to download "
                "the full package during metadata scanning."
            )

        if len(content) != length:
            raise RuntimeError(
                f"Range response length mismatch: expected {length}, got {len(content)}"
            )

        return content


def _decode_c_string(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def _format_system_version(raw: int) -> Optional[str]:
    if raw == 0:
        return None

    major = (raw >> 24) & 0xFF
    minor = (raw >> 16) & 0xFF
    patch = (raw >> 8) & 0xFF

    if major == 0:
        return None

    if patch:
        return f"{major}.{minor:02d}.{patch:02d}"

    return f"{major}.{minor:02d}"


def parse_sfo(data: bytes) -> Dict[str, object]:
    if len(data) < 20:
        raise ValueError("PARAM.SFO is too small")

    magic, _version, key_offset, data_offset, entry_count = struct.unpack_from(
        "<5I", data, 0
    )

    if magic != 0x46535000:
        raise ValueError("Invalid PARAM.SFO magic")

    entries = {}

    for index in range(entry_count):
        offset = 20 + index * 16
        if offset + 16 > len(data):
            raise ValueError("PARAM.SFO entry table is truncated")

        key_rel, fmt, value_len, _max_len, value_rel = struct.unpack_from(
            "<HHIII", data, offset
        )

        key_start = key_offset + key_rel
        key_end = data.find(b"\x00", key_start)
        if key_end == -1:
            key_end = len(data)

        key = data[key_start:key_end].decode("utf-8", errors="replace")

        value_start = data_offset + value_rel
        value_end = value_start + value_len

        if value_start < 0 or value_end > len(data):
            raise ValueError(f"PARAM.SFO value for {key} is outside the file")

        raw = data[value_start:value_end]

        if fmt == 0x0204:
            value = _decode_c_string(raw)
        elif fmt == 0x0404:
            if len(raw) < 4:
                value = 0
            else:
                value = struct.unpack_from("<I", raw, 0)[0]
        else:
            value = raw

        entries[key] = value

    return entries


def extract_metadata(url: str, size: int) -> Dict[str, object]:
    reader = RangeReader(url, size)

    header = reader.read(0, 0x1000)

    magic = struct.unpack_from(">I", header, 0)[0]
    if magic != PKG_MAGIC:
        raise ValueError("Not a valid PS4 PKG (bad magic)")

    entry_count = struct.unpack_from(">I", header, 0x10)[0]
    table_offset = struct.unpack_from(">I", header, 0x18)[0]

    content_id = _decode_c_string(header[0x30:0x54])

    if entry_count == 0:
        raise ValueError("PKG contains no file-table entries")

    table_size = entry_count * 0x20
    table = reader.read(table_offset, table_size)

    sfo_offset = None
    sfo_size = None

    for index in range(entry_count):
        entry = table[index * 0x20:(index + 1) * 0x20]
        entry_id, _filename_offset, _flags1, _flags2, offset, entry_size = struct.unpack_from(
            ">IIIIII", entry, 0
        )

        if entry_id == PARAM_SFO_ID:
            sfo_offset = offset
            sfo_size = entry_size
            break

    if sfo_offset is None or sfo_size is None:
        raise ValueError("PKG does not contain a PARAM.SFO entry")

    sfo = reader.read(sfo_offset, sfo_size)
    params = parse_sfo(sfo)

    title = params.get("TITLE")
    title_id = params.get("TITLE_ID")
    version = params.get("VERSION")
    app_ver = params.get("APP_VER")
    system_ver = params.get("SYSTEM_VER")

    if not isinstance(title, str) or not title:
        title = None
    if not isinstance(title_id, str) or not title_id:
        title_id = None
    if not isinstance(version, str) or not version:
        version = None
    if not isinstance(app_ver, str) or not app_ver:
        app_ver = None
    if not isinstance(system_ver, int):
        system_ver = None

    # FPKGi's "version" should represent the application's version.
    # PS4 PARAM.SFO exposes that as APP_VER; VERSION is the package/disc revision.
    fpkgi_version = app_ver or version

    region = None
    if content_id:
        region = REGION_MAP.get(content_id[:2].upper())

    metadata = {
        "name": title,
        "title_id": title_id,
        "version": fpkgi_version,
        "min_fw": _format_system_version(system_ver) if system_ver is not None else None,
        "content_id": content_id or None,
        "category": params.get("CATEGORY"),
        "region": region,
    }

    return {key: value for key, value in metadata.items() if value not in (None, "")}
