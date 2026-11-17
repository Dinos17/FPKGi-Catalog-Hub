import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from akirabox_catalog_compare import compare_scan, parse_source_details, parse_source_identity


def write_catalog(root: Path, relative: str, data: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def test_parses_title_region_and_version():
    assert parse_source_identity("A Hat in Time - [US] [EN] [1.04].pkg") == ("a hat in time", "USA", "1.04")

def test_parses_nonstandard_scene_filename_without_treating_firmware_as_version():
    assert parse_source_details(
        "[SuperPSX]-Slime.Rancher_CUSA11650_v1.00_[5.05]-PS4.pkg"
    ) == ("slime rancher", None, "1.00", "CUSA11650")


def test_parses_all_names_from_the_reported_ten_file_scan():
    names = [
        "AVICII Invector - [US] [EN] [1.05].pkg",
        "ASTRONEER - [US] [EN] [1.64].pkg",
        "A Hat in Time - [US] [EN] [1.04].pkg",
        "A Short Hike - [EU] [EN] [1.01].pkg",
        "A Monster's Expedition - [EU] [EN] [1.03].pkg",
        "Absolute Drift - [US] [EN] [1.02].pkg",
        "ADR1FT - [US] [EN] [1.01].pkg",
        "ABZU - [EU] [EN] [1.01].pkg",
        "Absolute Drift - [US] [EN] [1.02].pkg",
        "[SuperPSX]-Slime.Rancher_CUSA11650_v1.00_[5.05]-PS4.pkg",
    ]
    parsed = [parse_source_details(name) for name in names]
    assert [item[0] for item in parsed] == [
        "avicii invector", "astroneer", "a hat in time", "a short hike",
        "a monster s expedition", "absolute drift", "adr1ft", "abzu",
        "absolute drift", "slime rancher",
    ]
    assert parsed[-1][2:] == ("1.00", "CUSA11650")


def test_title_id_match_handles_slime_rancher_filename(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/slime.pkg": {
            "name": "Slime Rancher", "title_id": "CUSA11650",
            "region": "EUR", "version": "01.20", "size": 100,
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    complete_folders = [
        {"path": ["PS4", "Games"]}, {"path": ["PS4", "DLC"]},
        {"path": ["PS5", "Games"]}, {"path": ["PS5", "DLC"]},
    ]
    report = compare_scan({"errors": [], "folders": complete_folders, "files": [{
        "id": "E9Qz9AWjwzOL",
        "name": "[SuperPSX]-Slime.Rancher_CUSA11650_v1.00_[5.05]-PS4.pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
    }]}, tmp_path)
    assert report["results"][0]["title_id"] == "CUSA11650"
    assert report["results"][0]["status"] == "outdated"
    assert report["results"][0]["reason"] == "version differs"
    assert report["missing_candidate_count"] == 0


def test_missing_version_or_size_is_not_claimed_unchanged(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/game.pkg": {
            "name": "Example Game", "region": "USA", "version": "01.00",
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "missing-metadata", "name": "Example Game - [US] [EN] [1.00].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": None,
    }]}, tmp_path)
    assert report["results"][0]["status"] == "unclassified"


def test_classifies_new_unchanged_and_outdated_without_writing(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/old.pkg": {"name": "A Hat in Time", "region": "USA", "version": "01.04", "size": 100},
        "https://archive.org/older.pkg": {"name": "ASTRONEER", "region": "USA", "version": "01.63", "size": 200},
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"): write_catalog(tmp_path, path, {})
    scan = {"errors": [], "folders": [{"path": ["PS4", "Games"]}, {"path": ["PS4", "DLC"]}, {"path": ["PS5", "Games"]}, {"path": ["PS5", "DLC"]}], "files": [
        {"id": "1", "name": "A Hat in Time - [US] [EN] [1.04].pkg", "platform": "PS4", "catalog_category": "games", "size_bytes": 100},
        {"id": "2", "name": "ASTRONEER - [US] [EN] [1.64].pkg", "platform": "PS4", "catalog_category": "games", "size_bytes": 200},
        {"id": "3", "name": "Brand New Game - [EU] [EN] [1.00].pkg", "platform": "PS4", "catalog_category": "games", "size_bytes": 300},
    ]}
    report = compare_scan(scan, tmp_path)
    assert report["counts"] == {"new": 1, "unchanged": 1, "outdated": 1, "duplicate": 0, "unclassified": 0}
    assert report["scan_complete_for_all_categories"] is True
    assert report["catalog_write_performed"] is False


def test_does_not_infer_missing_entries_from_partial_scan(tmp_path):
    for path in ("ps4/games.json", "ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"): write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [{"path": ["PS4", "Games"]}], "files": []}, tmp_path)
    assert report["missing_check_deferred"] is True
    assert report["missing_catalog_entries"] == []


def test_duplicate_catalog_matches_are_flagged(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {"https://archive.org/one.pkg": {"name": "Same Game", "region": "USA", "version": "01.00"}, "https://archive.org/two.pkg": {"name": "Same Game", "region": "USA", "version": "01.00"}})
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"): write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{"id": "X", "name": "Same Game - [US] [EN] [1.00].pkg", "platform": "PS4", "catalog_category": "games"}]}, tmp_path)
    assert report["results"][0]["status"] == "duplicate"

def test_shared_dlc_title_id_matches_the_specific_package_and_tracks_each_catalog_url(tmp_path):
    write_catalog(tmp_path, "ps4/dlc.json", {
        "https://archive.org/rb-pack-1.pkg": {
            "name": "Rock Band™ Expansion Pack 1", "title_id": "CUSA02084",
            "region": "USA", "version": "01.00", "size": 100,
        },
        "https://archive.org/rb-pack-2.pkg": {
            "name": "Rock Band™ Expansion Pack 2", "title_id": "CUSA02084",
            "region": "USA", "version": "01.00", "size": 200,
        },
    })
    for path in ("ps4/games.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    folders = [
        {"path": ["PS4", "Games"]}, {"path": ["PS4", "DLC"]},
        {"path": ["PS5", "Games"]}, {"path": ["PS5", "DLC"]},
    ]
    report = compare_scan({"errors": [], "folders": folders, "files": [{
        "id": "rb1", "name": "Rock Band 4 Expansion Pack 1_CUSA02084_v1.00.pkg",
        "platform": "PS4", "catalog_category": "dlc", "size_bytes": 100,
    }]}, tmp_path)

    assert report["results"][0]["status"] == "unchanged"
    assert report["missing_catalog_entries"] == [{
        "platform": "PS4", "category": "dlc",
        "name": "Rock Band™ Expansion Pack 2", "region": "USA",
        "catalog_url": "https://archive.org/rb-pack-2.pkg",
        "status": "not_seen_in_source",
    }]


def test_same_visible_name_with_different_title_ids_is_not_a_source_duplicate(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/game-a.pkg": {
            "name": "Same Game", "title_id": "CUSA11111",
            "region": "USA", "version": "01.00", "size": 100,
        },
        "https://archive.org/game-b.pkg": {
            "name": "Same Game", "title_id": "CUSA22222",
            "region": "USA", "version": "01.00", "size": 200,
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [
        {"id": "a", "name": "Same Game_CUSA11111_v1.00.pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 100},
        {"id": "b", "name": "Same Game_CUSA22222_v1.00.pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 200},
    ]}, tmp_path)

    assert [item["status"] for item in report["results"]] == ["unchanged", "unchanged"]
    assert report["counts"]["duplicate"] == 0
