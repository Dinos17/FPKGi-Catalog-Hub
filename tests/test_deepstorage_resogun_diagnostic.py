import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import deepstorage_resogun_diagnostic as diagnostic


def make_sfo(values):
    keys = bytearray()
    data = bytearray()
    indexes = bytearray()
    for key, value in values.items():
        key_offset = len(keys)
        keys.extend(key.encode("ascii") + b"\0")
        raw = value.encode("utf-8") + b"\0"
        indexes.extend(struct.pack("<HHIII", key_offset, 0x0204,
                                   len(raw), len(raw), len(data)))
        data.extend(raw)
    key_base = 0x14 + len(indexes)
    value_base = key_base + len(keys)
    return (struct.pack("<4sIIII", b"\0PSF", 0x00000101, key_base,
                        value_base, len(values)) + indexes + keys + data)


def test_independent_sfo_parser_reads_app_ver_and_identity():
    parsed = diagnostic.parse_sfo_independent(make_sfo({
        "TITLE": "RESOGUN", "TITLE_ID": "CUSA00038",
        "APP_VER": "01.00", "SYSTEM_VER": "09.000",
    }))
    assert parsed["TITLE"] == "RESOGUN"
    assert parsed["TITLE_ID"] == "CUSA00038"
    assert parsed["APP_VER"] == "01.00"


def test_independent_sfo_parser_rejects_bad_offsets():
    damaged = bytearray(make_sfo({"APP_VER": "01.00"}))
    struct.pack_into("<I", damaged, 8, 0xFFFFFFF0)
    with pytest.raises(diagnostic.DiagnosticError, match="out of bounds"):
        diagnostic.parse_sfo_independent(bytes(damaged))


def test_pkg_table_finds_param_sfo_entry():
    header = bytearray(0x20)
    struct.pack_into(">I", header, 0, 0x7F434E54)
    struct.pack_into(">I", header, 0x10, 1)
    struct.pack_into(">I", header, 0x18, 0x1000)
    assert diagnostic.parse_pkg_table(bytes(header)) == (1, 0x1000)
    table = struct.pack(">IIIIII8x", 0x1000, 0, 0, 0, 0x4000, 128)
    assert diagnostic.find_sfo_entry(table, 1) == (0x4000, 128)


def test_source_url_is_pinned_to_exact_deepstorage_host():
    diagnostic.validate_source_url(diagnostic.SOURCE_URL)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_source_url("https://example.com/package.pkg")


def test_range_reader_refuses_redirects_and_full_downloads():
    class Response:
        def __init__(self, status):
            self.status_code = status
            self.headers = {}
            self.url = diagnostic.SOURCE_URL
        def close(self):
            pass

    class Session:
        def __init__(self, status):
            self.status = status
            self.headers = {}
        def get(self, *args, **kwargs):
            assert kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Range"] == "bytes=0-31"
            return Response(self.status)

    for status in (200, 302):
        reader = diagnostic.BoundedRangeReader(
            diagnostic.SOURCE_URL, session=Session(status)
        )
        with pytest.raises(diagnostic.DiagnosticError, match="did not honor"):
            reader.read(0, 32)


def test_encrypted_param_sfo_is_refused():
    table = struct.pack(">IIIIII8x", 0x1000, 0, 0x80000000, 0, 0x4000, 128)
    with pytest.raises(diagnostic.DiagnosticError, match="encrypted"):
        diagnostic.find_sfo_entry(table, 1)
