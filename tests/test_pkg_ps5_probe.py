import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pkg_ps5_probe as probe
import pkg_remote_metadata as pkg


CONTENT_ID = "UP9000-PPSA01615_00-EXAMPLE000000000"


class FakeReader:
    def __init__(self, data, *, discover_size_on_first_read=False):
        self.data = data
        self.total_size = None if discover_size_on_first_read else len(data)
        self.discover_size_on_first_read = discover_size_on_first_read
        self.reads = []

    def read(self, start, length):
        self.reads.append((start, length))
        if self.discover_size_on_first_read:
            self.total_size = len(self.data)
            self.discover_size_on_first_read = False
        if start < 0 or length <= 0 or start + length > len(self.data):
            raise pkg.MetadataError("range outside synthetic package")
        return self.data[start:start + length]


def make_sfo(fields=None):
    fields = fields or {"TITLE_ID": "PPSA01615", "APP_VER": "6.00", "CATEGORY": "gp"}
    key_table = bytearray()
    data_table = bytearray()
    index = bytearray()
    for key, value in fields.items():
        key_offset = len(key_table)
        key_table.extend(key.encode("ascii") + b"\0")
        raw = value.encode("utf-8") + b"\0"
        value_offset = len(data_table)
        data_table.extend(raw)
        index.extend(struct.pack("<HHIII", key_offset, 0x0204, len(raw), len(raw), value_offset))
    key_table_offset = 0x14 + len(fields) * 0x10
    data_table_offset = key_table_offset + len(key_table)
    header = struct.pack(
        "<4sIIII", b"\0PSF", 0x00000101,
        key_table_offset, data_table_offset, len(fields),
    )
    return header + index + key_table + data_table


def make_ps5_package(*, encrypted=False, include_sfo=True, fih=True):
    sfo = make_sfo()
    entry_count = 2 if include_sfo else 1
    entry_table_offset = probe.CNT_HEADER_SIZE
    table_length = entry_count * probe.ENTRY_SIZE
    names_offset = entry_table_offset + table_length
    names = b"\0param.sfo\0"
    sfo_offset = names_offset + len(names)
    total_cnt_size = sfo_offset + (len(sfo) if include_sfo else 0)
    cnt_offset = 0x12000 if fih else 0
    package = bytearray(cnt_offset + total_cnt_size)

    if fih:
        header = bytearray(probe.FIH_HEADER_READ_SIZE)
        header[:4] = probe.FIH_MAGIC
        header[probe.FIH_SIGNED_BYTE_OFFSET] = 0x80
        struct.pack_into("<H", header, probe.FIH_FORMAT_VERSION_OFFSET, 3)
        struct.pack_into("<Q", header, probe.FIH_PFS_IMAGE_OFFSET, 0x10000)
        struct.pack_into("<Q", header, probe.FIH_PFS_IMAGE_SIZE, 0x2000)
        struct.pack_into("<Q", header, probe.FIH_EMBEDDED_CNT_OFFSET, cnt_offset)
        package[:len(header)] = header

    cnt_header = bytearray(probe.CNT_HEADER_SIZE)
    cnt_header[:4] = probe.CNT_MAGIC
    struct.pack_into(">I", cnt_header, probe.CNT_ENTRY_COUNT_OFFSET, entry_count)
    struct.pack_into(">I", cnt_header, probe.CNT_ENTRY_TABLE_OFFSET, entry_table_offset)
    content_id = CONTENT_ID.encode("ascii")
    cnt_header[
        probe.CNT_CONTENT_ID_OFFSET:
        probe.CNT_CONTENT_ID_OFFSET + len(content_id)
    ] = content_id
    package[cnt_offset:cnt_offset + len(cnt_header)] = cnt_header

    entries = bytearray()
    entries.extend(struct.pack(">IIIIII", 0x200, 0, 0, 0, names_offset, len(names)) + bytes(8))
    if include_sfo:
        flags1 = probe.ENTRY_ENCRYPTED_FLAG if encrypted else 0
        entries.extend(struct.pack(
            ">IIIIII", pkg.PARAM_SFO_ID, 1, flags1, 0, sfo_offset, len(sfo)
        ) + bytes(8))
    package[cnt_offset + entry_table_offset:cnt_offset + entry_table_offset + len(entries)] = entries
    package[cnt_offset + names_offset:cnt_offset + names_offset + len(names)] = names
    if include_sfo:
        package[cnt_offset + sfo_offset:cnt_offset + sfo_offset + len(sfo)] = sfo
    return bytes(package), cnt_offset


def test_extracts_unencrypted_ps5_param_sfo_from_fih_cnt_container():
    package, cnt_offset = make_ps5_package()
    reader = FakeReader(package)
    result = probe.probe_ps5_reader(
        reader, expected_title_id="PPSA01615", expected_size=len(package)
    )
    assert result["status"] == "extracted"
    assert result["identity_verified"] is True
    assert result["title_id"] == "PPSA01615"
    assert result["version"] == "6.00"
    assert result["package_size"] == len(package)
    assert result["cnt_offset"] == cnt_offset
    assert result["cnt_content_id"] == CONTENT_ID
    assert result["cnt_entry_ids"] == [0x200, pkg.PARAM_SFO_ID]
    assert result["region"] is None
    assert all(length <= pkg.MAX_ENTRY_BYTES for _, length in reader.reads)


def test_encrypted_param_sfo_is_reported_without_decryption():
    package, _ = make_ps5_package(encrypted=True)
    reader = FakeReader(package)
    result = probe.probe_ps5_reader(reader, expected_title_id="PPSA01615", expected_size=len(package))
    assert result["status"] == "param_sfo_encrypted"
    assert result["param_sfo_entry"]["encrypted"] is True
    assert "title_id" not in result
    sfo_entry = result["param_sfo_entry"]
    sfo_range = (result["cnt_offset"] + sfo_entry["data_offset"], sfo_entry["data_size"])
    assert sfo_range not in reader.reads


def test_missing_param_sfo_is_reported():
    package, _ = make_ps5_package(include_sfo=False)
    result = probe.probe_ps5_reader(FakeReader(package))
    assert result["status"] == "param_sfo_missing"


def test_supports_cnt_metadata_container_without_fih():
    package, _ = make_ps5_package(fih=False)
    result = probe.probe_ps5_reader(
        FakeReader(package), expected_title_id="PPSA01615", expected_size=len(package)
    )
    assert result["status"] == "extracted"
    assert "fih_signed_byte" not in result
    assert result["identity_verified"] is True


def test_rejects_unrecognized_outer_magic():
    package, _ = make_ps5_package()
    damaged = b"FAIL" + package[4:]
    with pytest.raises(pkg.MetadataError, match="recognized PS5"):
        probe.probe_ps5_reader(FakeReader(damaged))


def test_rejects_invalid_embedded_cnt_magic():
    package, cnt_offset = make_ps5_package()
    damaged = bytearray(package)
    damaged[cnt_offset:cnt_offset + 4] = b"FAIL"
    with pytest.raises(pkg.MetadataError, match="valid CNT"):
        probe.probe_ps5_reader(FakeReader(bytes(damaged)))


def test_rejects_entry_table_outside_package():
    package, cnt_offset = make_ps5_package()
    damaged = bytearray(package)
    struct.pack_into(">I", damaged, cnt_offset + probe.CNT_ENTRY_TABLE_OFFSET, 0xFFFFFFF0)
    with pytest.raises(pkg.MetadataError, match="entry table is outside"):
        probe.probe_ps5_reader(FakeReader(bytes(damaged)))


@pytest.mark.parametrize(
    ("expected_title_id", "expected_size"),
    [("PPSA00000", None), (None, 1)],
)
def test_catalog_identity_mismatch_is_reported(expected_title_id, expected_size):
    package, _ = make_ps5_package()
    result = probe.probe_ps5_reader(
        FakeReader(package), expected_title_id=expected_title_id, expected_size=expected_size
    )
    assert result["status"] == "identity_mismatch"
    assert result["identity_verified"] is False
    assert result["identity_errors"]


def test_rejects_multiple_param_sfo_entries():
    package, cnt_offset = make_ps5_package()
    damaged = bytearray(package)
    cnt_header = cnt_offset
    struct.pack_into(">I", damaged, cnt_header + probe.CNT_ENTRY_COUNT_OFFSET, 3)
    table_offset = struct.unpack_from(">I", damaged, cnt_header + probe.CNT_ENTRY_TABLE_OFFSET)[0]
    first_sfo = damaged[cnt_offset + table_offset + probe.ENTRY_SIZE:
                        cnt_offset + table_offset + 2 * probe.ENTRY_SIZE]
    damaged[cnt_offset + table_offset + 2 * probe.ENTRY_SIZE:
            cnt_offset + table_offset + 3 * probe.ENTRY_SIZE] = first_sfo
    with pytest.raises(pkg.MetadataError, match="multiple PARAM.SFO"):
        probe.probe_ps5_reader(FakeReader(bytes(damaged)))


def test_discovers_total_size_from_first_range_response():
    package, _ = make_ps5_package()
    reader = FakeReader(package, discover_size_on_first_read=True)
    result = probe.probe_ps5_reader(
        reader, expected_title_id="PPSA01615", expected_size=len(package)
    )
    assert result["status"] == "extracted"
    assert result["package_size"] == len(package)
