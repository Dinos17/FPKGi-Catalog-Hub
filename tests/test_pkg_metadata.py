import struct
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from pkg_metadata import RangeReader, _format_system_version, parse_sfo  # noqa: E402


def build_sfo(entries):
    keys = bytearray()
    key_offsets = {}
    values = bytearray()
    records = []

    for key, value in entries.items():
        key_offsets[key] = len(keys)
        keys.extend(key.encode("utf-8") + b"\x00")

        if isinstance(value, str):
            raw = value.encode("utf-8") + b"\x00"
            fmt = 0x0204
        else:
            raw = struct.pack("<I", value)
            fmt = 0x0404

        value_offset = len(values)
        values.extend(raw)
        records.append((key, fmt, len(raw), len(raw), value_offset))

    header_size = 20
    entry_table_size = len(records) * 16
    key_offset = header_size + entry_table_size
    data_offset = key_offset + len(keys)

    output = bytearray(
        struct.pack("<5I", 0x46535000, 0x00000101, key_offset, data_offset, len(records))
    )

    for key, fmt, value_len, max_len, value_offset in records:
        output.extend(
            struct.pack(
                "<HHIII",
                key_offsets[key],
                fmt,
                value_len,
                max_len,
                value_offset,
            )
        )

    output.extend(keys)
    output.extend(values)
    return bytes(output)


def test_parse_sfo_decodes_string_and_integer_fields():
    data = build_sfo({
        "TITLE": "Example Game",
        "TITLE_ID": "CUSA12345",
        "APP_VER": "1.20",
        "SYSTEM_VER": 0x00060000,
    })

    result = parse_sfo(data)

    assert result["TITLE"] == "Example Game"
    assert result["TITLE_ID"] == "CUSA12345"
    assert result["APP_VER"] == "1.20"
    assert result["SYSTEM_VER"] == 0x00060000


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"too-small",
        struct.pack("<5I", 0, 0, 0, 0, 0),
    ],
)
def test_parse_sfo_rejects_invalid_data(data):
    with pytest.raises(ValueError):
        parse_sfo(data)


def test_parse_sfo_rejects_key_offset_outside_file():
    data = struct.pack("<5I", 0x46535000, 0x101, 0x1000, 20, 1) + (b"\x00" * 16)
    with pytest.raises(ValueError, match="key"):
        parse_sfo(data)


def test_parse_sfo_rejects_truncated_entry_table():
    data = struct.pack("<5I", 0x46535000, 0x101, 20, 36, 1)
    with pytest.raises(ValueError, match="entry table"):
        parse_sfo(data)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, None),
        (0x05000000, "5.00"),
        (0x06040000, "6.04"),
        (0x06040001, "6.04.00"),
    ],
)
def test_format_system_version(raw, expected):
    assert _format_system_version(raw) == expected


def test_range_reader_rejects_out_of_bounds():
    reader = RangeReader("https://example.com/file.pkg", 100)

    with pytest.raises(ValueError):
        reader.read(90, 20)


class FakeResponse:
    def __init__(self, content, content_range, status_code=206):
        self.content = content
        self.headers = {"Content-Range": content_range}
        self.status_code = status_code

    def raise_for_status(self):
        pass


@pytest.mark.parametrize(
    "content_range",
    [
        "bytes 10-19/99",
        "bytes 11-19/100",
        "bytes 10-18/100",
        "bytes 10-19/*",
        "bytes 10-19/100/extra",
        "invalid",
    ],
)
def test_range_reader_rejects_inexact_content_range(monkeypatch, content_range):
    monkeypatch.setattr(
        "pkg_metadata.requests.get",
        lambda *args, **kwargs: FakeResponse(b"x" * 10, content_range),
    )

    reader = RangeReader("https://example.com/file.pkg", 100)

    with pytest.raises(RuntimeError, match="Content-Range"):
        reader.read(10, 10)


def test_range_reader_accepts_exact_content_range(monkeypatch):
    monkeypatch.setattr(
        "pkg_metadata.requests.get",
        lambda *args, **kwargs: FakeResponse(b"x" * 10, "bytes 10-19/100"),
    )

    reader = RangeReader("https://example.com/file.pkg", 100)

    assert reader.read(10, 10) == b"x" * 10
