import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from catalog_identity import extract_content_id, extract_title_id, identity_keys


def test_extract_ids_from_pkg_url():
    url = (
        "https://example.invalid/"
        "EP9000-CUSA00002_00-KZ4RELEASE000041-A0107-V0100.pkg"
    )
    assert extract_title_id(url) == "CUSA00002"
    assert extract_content_id(url) == "EP9000-CUSA00002_00-KZ4RELEASE000041"


def test_content_id_is_strongest_identity():
    keys = identity_keys(
        "https://example.invalid/EP9000-CUSA00002_00-KZ4RELEASE000041.pkg",
        {
            "title_id": "CUSA00002",
            "content_id": "EP9000-CUSA00002_00-KZ4RELEASE000041",
            "version": "01.07",
        },
        "games",
    )
    assert ("content", "EP9000-CUSA00002_00-KZ4RELEASE000041") in keys


def test_base_game_uses_title_category_identity():
    keys = identity_keys(
        "https://example.invalid/game.pkg",
        {"title_id": "CUSA12345", "version": "01.00"},
        "games",
    )
    assert ("title-category", "games", "CUSA12345") in keys
    assert ("title-version", "games", "CUSA12345", "01.00") in keys


def test_updates_do_not_collapse_by_title_id_alone():
    keys = identity_keys(
        "https://example.invalid/update.pkg",
        {"title_id": "CUSA12345", "version": "01.03"},
        "updates",
    )
    assert ("title-version", "updates", "CUSA12345", "01.03") in keys
    assert not any(key[0] == "title-category" for key in keys)
