import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pkg_remote_metadata as pkg


def make_sfo(values):
    keys = bytearray()
    data = bytearray()
    indexes = bytearray()
    key_offsets = []
    for key, value in values.items():
        key_offsets.append(len(keys))
        keys.extend(key.encode("ascii") + b"\0")
    for (key, value), key_offset in zip(values.items(), key_offsets):
        raw = value.encode("utf-8") + b"\0"
        indexes.extend(struct.pack("<HHIII", key_offset, 0x0204, len(raw), len(raw), len(data)))
        data.extend(raw)
    key_start = 0x14 + len(indexes)
    data_start = key_start + len(keys)
    return (struct.pack("<4sIIII", b"\0PSF", 0x00000101, key_start,
                        data_start, len(values)) + indexes + keys + data)


def make_pkg(sfo, *, encrypted=False):
    header = bytearray(0x1000)
    struct.pack_into(">I", header, 0, pkg.PKG_MAGIC)
    struct.pack_into(">I", header, 0x0C, 3)
    struct.pack_into(">I", header, 0x10, 1)
    struct.pack_into(">I", header, 0x18, 0x1000)
    entry_offset = 0x2000
    flags1 = 0x80000000 if encrypted else 0
    entry = struct.pack(">IIIIIIQ", pkg.PARAM_SFO_ID, 0, flags1, 0,
                        entry_offset, len(sfo), 0)
    return bytes(header), entry, entry_offset, sfo


def test_parse_pkg_header_reads_big_endian_table_fields():
    header = bytearray(0x1000)
    struct.pack_into(">I", header, 0, pkg.PKG_MAGIC)
    struct.pack_into(">I", header, 0x0C, 7)
    struct.pack_into(">I", header, 0x10, 2)
    struct.pack_into(">I", header, 0x18, 0x2000)
    assert pkg.parse_pkg_header(bytes(header)) == (2, 0x2000, 7)


@pytest.mark.parametrize("header", [b"", b"NOPE" + bytes(4092)])
def test_rejects_invalid_pkg_header(header):
    with pytest.raises(pkg.MetadataError):
        pkg.parse_pkg_header(header)


def test_rejects_unreasonable_entry_count():
    header = bytearray(0x1000)
    struct.pack_into(">I", header, 0, pkg.PKG_MAGIC)
    struct.pack_into(">I", header, 0x10, pkg.MAX_ENTRIES + 1)
    with pytest.raises(pkg.MetadataError, match="entry count"):
        pkg.parse_pkg_header(bytes(header))


def test_parse_sfo_reads_strings():
    fields = pkg.parse_sfo(make_sfo({
        "TITLE": "TowerFall Dark World Expansion",
        "TITLE_ID": "CUSA00466",
        "APP_VER": "01.02",
        "SYSTEM_VER": "09.000",
        "CATEGORY": "ac",
    }))
    assert fields["TITLE"] == "TowerFall Dark World Expansion"
    assert fields["TITLE_ID"] == "CUSA00466"
    assert fields["APP_VER"] == "01.02"
    assert fields["SYSTEM_VER"] == "09.000"


def test_rejects_bad_sfo_magic_and_out_of_bounds():
    with pytest.raises(pkg.MetadataError, match="header"):
        pkg.parse_sfo(b"not sfo")
    damaged = bytearray(make_sfo({"TITLE": "Bad"}))
    struct.pack_into("<I", damaged, 8, 0xFFFFFFF0)
    with pytest.raises(pkg.MetadataError):
        pkg.parse_sfo(bytes(damaged))


class FakeReader:
    def __init__(self, header, entry, entry_offset, sfo, encrypted=False):
        self.header = header
        self.entry = entry
        self.entry_offset = entry_offset
        self.sfo = sfo
        self.total_size = 123456
        self.encrypted = encrypted

    def read(self, start, length):
        if start == 0:
            return self.header
        if start == 0x1000:
            return self.entry
        if start == self.entry_offset:
            return self.sfo
        raise AssertionError((start, length))


def test_extract_ps4_metadata_reads_only_param_sfo_entry():
    sfo = make_sfo({"TITLE": "Rock Band 4 Expansion Pack 1",
                    "TITLE_ID": "CUSA02084", "APP_VER": "01.03",
                    "SYSTEM_VER": "09.000", "CATEGORY": "ac"})
    header, entry, offset, sfo = make_pkg(sfo)
    result = pkg.extract_ps4_metadata(FakeReader(header, entry, offset, sfo))
    assert result["title_id"] == "CUSA02084"
    assert result["version"] == "01.03"
    assert result["min_fw"] == "09.000"
    assert result["region"] is None
    assert result["package_size"] == 123456


def test_refuses_encrypted_param_sfo():
    sfo = make_sfo({"APP_VER": "01.00"})
    header, entry, offset, sfo = make_pkg(sfo, encrypted=True)
    with pytest.raises(pkg.MetadataError, match="encrypted"):
        pkg.extract_ps4_metadata(FakeReader(header, entry, offset, sfo))


def test_range_reader_requires_http_206_and_exact_content_range():
    class Response:
        status_code = 206
        headers = {"Content-Range": "bytes 0-3/100"}
        def iter_content(self, chunk_size):
            yield b"ABCD"
        def close(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    reader = pkg.RangeReader("https://archive.org/test.pkg", session=Session())
    assert reader.read(0, 4) == b"ABCD"
    assert reader.total_size == 100


def test_range_reader_rejects_server_ignoring_range():
    class Response:
        status_code = 200
        headers = {}
        def close(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    reader = pkg.RangeReader("https://archive.org/test.pkg", session=Session())
    with pytest.raises(pkg.MetadataError, match="did not honor Range"):
        reader.read(0, 4)


def test_rejects_unapproved_hosts_and_http():
    assert not pkg._host_allowed("http://archive.org/file.pkg")
    assert not pkg._host_allowed("https://evilarchive.org/file.pkg")
    assert pkg._host_allowed("https://ia800000.us.archive.org/file.pkg")
    assert not pkg._host_allowed("https://stor11.mochaprox.li/file.pkg")
    assert pkg._host_allowed("https://stor11.mochaprox.li/file.pkg",
                             {"stor11.mochaprox.li"})


def test_catalog_targets_finds_only_incomplete_identity_rows(tmp_path):
    import json
    (tmp_path / "ps4").mkdir()
    (tmp_path / "ps5").mkdir()
    (tmp_path / "ps4" / "dlc.json").write_text(json.dumps({"DATA": {
        "https://archive.org/a.pkg": {"title_id": "CUSA1", "region": "USA",
                                      "version": "", "size": 100},
        "https://archive.org/b.pkg": {"title_id": "CUSA2", "region": "USA",
                                      "version": "01.00", "size": 100},
    }}))
    targets = pkg.catalog_targets(tmp_path)
    assert len(targets) == 1
    assert targets[0]["missing"] == ["version"]


def test_main_direct_source_mode_inspects_one_source_and_writes_report(monkeypatch, tmp_path):
    import json

    captured = {}
    def fake_inspect(target):
        captured.update(target)
        return {"status": "extracted", "title_id": "CUSA04513"}

    monkeypatch.setattr(pkg, "inspect_target", fake_inspect)
    output = tmp_path / "direct-source.json"
    monkeypatch.setattr(sys, "argv", [
        "pkg_remote_metadata.py", "--source",
        "d5ac3913-802b-4d68-9803-8f5c07c7640e",
        "--output", str(output),
    ])

    assert pkg.main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["read_only"] is True
    assert report["targets_found"] == 1
    assert captured["source"] == "d5ac3913-802b-4d68-9803-8f5c07c7640e"
    assert captured["platform"] == "PS4"
    assert report["results"][0]["title_id"] == "CUSA04513"
