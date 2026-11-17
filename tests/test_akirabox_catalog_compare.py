import json
from pathlib import Path

import pytest

from akirabox_catalog_compare import compare_scan, parse_source_identity


def write_catalog(root: Path, relative: str, data: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def test_parses_title_region_and_version():
    assert parse_source_identity("A Hat in Time - [US] [EN] [1.04].pkg") == (
        "a hat in time", "USA", "1.04"
    )


def test_classifies_new_unchanged_and_outdated_without_writing(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/old.pkg": {
            "name": "A Hat in Time", "region": "USA", "version": "01.04",
            "size": 100,
        },
        "https://archive.org/older.pkg": {
            "name": "ASTRONEER", "region": "USA", "version": "01.63",
            "size": 200,
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    scan = {
        "errors": [],
        "folders": [
            {"path": ["PS4", "Games"]}, {"path": ["PS4", "DLC"]},
            {"path": ["PS5", "Games"]}, {"path": ["PS5", "DLC"]},
        ],
        "files": [
            {"id": "1", "name": "A Hat in Time - [US] [EN] [1.04].pkg",
             "platform": "PS4", "catalog_category": "games", "size_bytes": 100},
            {"id": "2", "name": "ASTRONEER - [US] [EN] [1.64].pkg",
             "platform": "PS4", "catalog_category": "games", "size_bytes": 200},
            {"id": "3", "name": "Brand New Game - [EU] [EN] [1.00].pkg",
             "platform": "PS4", "catalog_category": "games", "size_bytes": 300},
        ],
    }
    report = compare_scan(scan, tmp_path)
    assert report["counts"] == {
        "new": 1, "unchanged": 1, "outdated": 1, "duplicate": 0, "unclassified": 0
    }
    assert report["scan_complete_for_all_categories"] is True
    assert report["catalog_write_performed"] is False


def test_does_not_infer_missing_entries_from_partial_scan(tmp_path):
    for path in ("ps4/games.json", "ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [{"path": ["PS4", "Games"]}], "files": []}, tmp_path)
    assert report["missing_check_deferred"] is True
    assert report["missing_catalog_entries"] == []


def test_duplicate_catalog_matches_are_flagged(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/one.pkg": {"name": "Same Game", "region": "USA", "version": "01.00"},
        "https://archive.org/two.pkg": {"name": "Same Game", "region": "USA", "version": "01.00"},
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "X", "name": "Same Game - [US] [EN] [1.00].pkg",
        "platform": "PS4", "catalog_category": "games",
    }]}, tmp_path)
    assert report["results"][0]["status"] == "duplicate"
