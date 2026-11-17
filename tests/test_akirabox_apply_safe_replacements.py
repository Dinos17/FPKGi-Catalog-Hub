import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from akirabox_apply_safe_replacements import apply_safe_replacements


def write_catalog(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def make_case(tmp_path):
    old_url = "https://archive.org/download/game/game.pkg"
    new_url = "https://akirabox.to/abc123/file"
    record = {
        "name": "Example Game", "title_id": "CUSA12345", "region": "USA",
        "version": "01.00", "size": 100, "min_fw": "9.00",
    }
    write_catalog(tmp_path, "ps4/games.json", {old_url: record})
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    scan = {"files": [{
        "id": "file-1", "name": "Example Game - [US] [EN] [1.00].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
    }]}
    comparison = {"results": [{
        "status": "outdated", "file_id": "file-1", "name": scan["files"][0]["name"],
        "platform": "PS4", "category": "games",
        "source_metadata": {"title": "example game", "region": "USA", "version": "1.00",
                            "size_bytes": 100, "platform": "PS4", "category": "games"},
        "catalog_metadata": {**record, "size_bytes": 100,
                             "parsed_filename_metadata": {"title": "example game",
                                                          "region": "USA", "version": "1.00"}},
        "catalog_url": old_url,
        "metadata_differences": [],
        "proposed_replacement": {"from": old_url, "to": new_url},
    }]}
    return old_url, new_url, scan, comparison


def test_applies_only_exact_safe_url_replacement(tmp_path):
    old_url, new_url, scan, comparison = make_case(tmp_path)
    report = apply_safe_replacements(scan, comparison, tmp_path)
    data = json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]
    assert report["applied_count"] == 1
    assert old_url not in data
    assert data[new_url]["name"] == "Example Game"


def test_rejects_metadata_mismatch(tmp_path):
    old_url, new_url, scan, comparison = make_case(tmp_path)
    comparison["results"][0]["metadata_differences"] = [
        {"field": "version", "source_value": "1.01", "catalog_value": "01.00"}
    ]
    report = apply_safe_replacements(scan, comparison, tmp_path)
    data = json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]
    assert report["applied_count"] == 0
    assert old_url in data
    assert new_url not in data


def test_rejects_duplicate_filename_even_when_metadata_matches(tmp_path):
    old_url, new_url, scan, comparison = make_case(tmp_path)
    scan["files"].append({
        "id": "file-2", "name": "Example Game - [US] [EN] [1.00] (1).pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
    })
    report = apply_safe_replacements(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert old_url in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_does_not_overwrite_existing_destination_url(tmp_path):
    old_url, new_url, scan, comparison = make_case(tmp_path)
    path = tmp_path / "ps4/games.json"
    payload = json.loads(path.read_text())
    payload["DATA"][new_url] = {"name": "Different existing record"}
    path.write_text(json.dumps(payload))
    report = apply_safe_replacements(scan, comparison, tmp_path)
    data = json.loads(path.read_text())["DATA"]
    assert report["applied_count"] == 0
    assert old_url in data
    assert data[new_url]["name"] == "Different existing record"
