import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import deepstorage_version_field_audit as audit


EXPECTED_TARGETS = [
    ("Diablo III Reaper of Souls - [EU] [EN] [1.46].pkg", "https://share.deepstorage.dev/7d3c899ae7a5b89f535c674cfd4f26b4/Diablo%20III%20Reaper%20of%20Souls%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.46%5D.pkg"),
    ("RESOGUN - [US] [EN] [1.10].pkg", "https://share.deepstorage.dev/91be0fdbedb6a37d47530386c9d135e4/RESOGUN%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.10%5D.pkg"),
    ("Disco Elysium - [US] [EN] [1.19].pkg", "https://share.deepstorage.dev/5c9f2e4e9621a42a5d340801245df6a2/Disco%20Elysium%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.19%5D.pkg"),
    ("Disc Jam - [EU] [EN] [1.20].pkg", "https://share.deepstorage.dev/90fa921f58a667671f13f25f8edc766d/Disc%20Jam%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.20%5D.pkg"),
    ("Diablo II - [US] [EN] [1.23].pkg", "https://share.deepstorage.dev/b875b9724654170de8740ad88e43b85f/Diablo%20II%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.23%5D.pkg"),
    ("Devil May Cry Trilogy - [EU] [EN] [1.01].pkg", "https://share.deepstorage.dev/989a7c1972ff0a388e9569e0e6c6e74e/Devil%20May%20Cry%20Trilogy%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Devil May Cry 4 - [JP] [EN] [1.02].pkg", "https://share.deepstorage.dev/55a5060be7921f4c967415ac28e93d97/Devil%20May%20Cry%204%20-%20%5BJP%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("Destroy All Humans! - [US] [EN] [1.08].pkg", "https://share.deepstorage.dev/656fee201ae1ba012b8b95c04447db8d/Destroy%20All%20Humans%21%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.08%5D.pkg"),
    ("Despot's Game - [EU] [EN] [1.07].pkg", "https://share.deepstorage.dev/49f3a189773d42f91f08f5dfb0627c7e/Despot%27s%20Game%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.07%5D.pkg"),
    ("Deep Rock Galactic - [EU] [EN] [1.43].pkg", "https://share.deepstorage.dev/af6dbb4c3a5463b6d4cf52732ef0c7f6/Deep%20Rock%20Galactic%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.43%5D.pkg"),
    ("Death's Door - [US] [EN] [1.01].pkg", "https://share.deepstorage.dev/15d0307ee292781fbe9cf719781a66fd/Death%27s%20Door%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Dead Rising 2 - [US] [EN] [1.02].pkg", "https://share.deepstorage.dev/00910aee6645204672d7bf2758976dd8/Dead%20Rising%202%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("Dead Rising 2 Off The Record - [US] [EN] [1.02].pkg", "https://share.deepstorage.dev/e0d20910fcb2e4c33276b8cf79ff0eb3/Dead%20Rising%202%20Off%20The%20Record%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("Dead Nation - [US] [EN] [1.03].pkg", "https://share.deepstorage.dev/9e516879526bd1a7ceed176131fa5de5/Dead%20Nation%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.03%5D.pkg"),
    ("Deadlight - [EU] [EN] [1.02].pkg", "https://share.deepstorage.dev/87c34f45af44bc682770d708f9baa0ef/Deadlight%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("DARK SOULS II - [EU] [EN] [1.02].pkg", "https://share.deepstorage.dev/9a808674c3006a86a161a89911d169e0/DARK%20SOULS%20II%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("DARK SOULS - [EU] [EN] [1.03].pkg", "https://share.deepstorage.dev/cc4d8921b69cd33303c17a63eeda6142/DARK%20SOULS%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.03%5D.pkg"),
    ("Darksiders Genesis - [EU] [EN] [1.03].pkg", "https://share.deepstorage.dev/9764e0c15331de7bbac83d0c362fbfce/Darksiders%20Genesis%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.03%5D.pkg"),
    ("Darksiders - [EU] [EN] [1.01].pkg", "https://share.deepstorage.dev/13b6666a51323df8e28742fd595f8df0/Darksiders%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Assassin's Creed Rogue - [EU] [EN] [1.01].pkg", "https://share.deepstorage.dev/af49cbe2ea7e205208c37147427a010a/Assassin%27s%20Creed%20Rogue%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Ancestors Legacy - [EU] [EN] [1.05].pkg", "https://share.deepstorage.dev/aa1208a42eba435435cb79ebda826e6c/Ancestors%20Legacy%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.05%5D.pkg"),
    ("Amnesia The Bunker - [US] [EN] [1.81].pkg", "https://share.deepstorage.dev/a9542932441da57b6b5a7557c7c8b821/Amnesia%20The%20Bunker%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.81%5D.pkg"),
]


def test_filename_version_is_extracted_from_final_bracket_tag():
    assert audit.filename_version("RESOGUN - [US] [EN] [1.10].pkg") == "1.10"
    assert audit.filename_version("No version.pkg") is None


def test_audit_has_exactly_the_22_blocked_sync_targets():
    assert audit.TARGETS == EXPECTED_TARGETS
    for name, url in audit.TARGETS:
        audit.parser.validate_source_url(url)
        assert name.endswith(".pkg")


def test_markdown_cell_neutralizes_untrusted_multiline_metadata():
    rendered = audit.markdown_cell("gd\n| [fake](https://evil.example) **bold**")
    assert "\n" not in rendered
    assert "\\|" in rendered
    assert "\\[" in rendered
    assert "\\:" in rendered


def test_duplicate_audited_sfo_fields_are_rejected():
    entries = [("CATEGORY", b"gd\\0"), ("CATEGORY", b"gp\\0")]
    count = len(entries)
    key_base = 0x14 + count * 16
    key_data = bytearray()
    value_data = bytearray()
    indexes = bytearray()
    for key, value in entries:
        key_bytes = key.encode("ascii") + b"\\0"
        key_offset = len(key_data)
        value_offset = len(value_data)
        key_data.extend(key_bytes)
        value_data.extend(value)
        indexes.extend(struct.pack("<HHIII", key_offset, 0x0204, len(value), len(value), value_offset))
    value_base = key_base + len(key_data)
    header = b"\\x00PSF" + struct.pack("<IIII", 0x00000101, key_base, value_base, count)
    payload = header + indexes + key_data + value_data
    with pytest.raises(audit.parser.DiagnosticError, match="duplicate CATEGORY"):
        audit.parser.parse_sfo_independent(payload)
