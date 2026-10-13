import struct

import pytest

from tools.archive_to_hf import parse_sfo


def make_sfo(entries):
    """Build a minimal SFO; entries are (key, format, value_bytes)."""
    key_table = bytearray()
    data_table = bytearray()
    records = []
    for key, fmt, value in entries:
        key_index = len(key_table)
        key_table.extend(key.encode("utf-8") + b"\x00")
        value_offset = len(data_table)
        data_table.extend(value)
        records.append((key_index, fmt, len(value), len(value), value_offset))

    key_offset = 20 + len(records) * 16
    data_offset = key_offset + len(key_table)
    header = struct.pack("<5I", 0x46535000, 0x00000101, key_offset, data_offset, len(records))
    table = b"".join(struct.pack("<HHIII", *record) for record in records)
    return header + table + key_table + data_table


def test_parse_sfo_keeps_title_and_title_id_as_strings():
    data = make_sfo([
        ("TITLE", 0x0204, b"Red Dead Redemption 2\x00"),
        ("TITLE_ID", 0x0204, b"CUSA08519\x00"),
        ("ATTRIBUTE", 0x0404, (7).to_bytes(4, "little")),
    ])

    parsed = parse_sfo(data)

    assert parsed["TITLE"] == "Red Dead Redemption 2"
    assert parsed["TITLE_ID"] == "CUSA08519"
    assert parsed["ATTRIBUTE"] == 7


def test_parse_sfo_rejects_truncated_header():
    with pytest.raises(ValueError, match="header is truncated"):
        parse_sfo(b"\x00PSF")


def test_parse_sfo_rejects_unknown_format():
    data = make_sfo([("TITLE", 0x0104, b"wrong\x00")])
    with pytest.raises(ValueError, match="Unsupported PARAM.SFO format"):
        parse_sfo(data)
