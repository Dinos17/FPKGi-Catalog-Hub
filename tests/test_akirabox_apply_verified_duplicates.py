import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from akirabox_apply_verified_duplicates import apply_verified_duplicates


def write_catalog(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def make_case(tmp_path):
    old_url = "https://archive.org/download/example/game.pkg"
    older_url = "https://akirabox.to/older/file"
    newer_url = "https://akirabox.to/newer/file"
    record = {
        "name": "Example Game", "title_id": "CUSA12345", "region": "USA",
        "version": "01.00", "size": 100, "min_fw": "9.00",
    }
    write_catalog(tmp_path, "ps4/games.json", {old_url: record})
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    scan = {"files": [
        {"id": "older", "name": "Example Game - [US] [EN] [1.00].pkg",
         "share_url": older_url, "created_at": "2026-01-01T10:00:00Z"},
        {"id": "newer", "name": "Example Game - [US] [EN] [1.00] (1).pkg",
         "share_url": newer_url, "created_at": "2026-02-01T10:00:00Z"},
    ]}
    comparison = {"results": [
        {"status": "matched", "file_id": "older", "platform": "PS4",
         "category": "games", "catalog_url": old_url,
         "catalog_metadata": {**record, "size_bytes": 100}},
        {"status": "duplicate", "file_id": "newer",
         "duplicate_of_file_id": "older",
         "binary_verification": {"status": "verified_identical"},
         "automatic_decision": {
             "decision": "keep_akirabox", "selected_file_id": "newer",
             "selected_file_name": scan["files"][1]["name"],
         }},
    ]}
    return old_url, older_url, newer_url, record, scan, comparison


def test_applies_verified_duplicate_and_moves_catalog_to_newest_upload(tmp_path):
    old_url, older_url, newer_url, record, scan, comparison = make_case(tmp_path)
    report = apply_verified_duplicates(scan, comparison, tmp_path)
    data = json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]
    assert report["applied_count"] == 1
    assert old_url not in data
    assert older_url not in data
    assert data[newer_url] == record
    assert report["akirabox_files_deleted"] is False


def test_collapses_only_identical_catalog_records_for_group_urls(tmp_path):
    old_url, older_url, newer_url, record, scan, comparison = make_case(tmp_path)
    path = tmp_path / "ps4/games.json"
    payload = json.loads(path.read_text())
    payload["DATA"][older_url] = record
    path.write_text(json.dumps(payload))
    report = apply_verified_duplicates(scan, comparison, tmp_path)
    data = json.loads(path.read_text())["DATA"]
    assert report["applied_count"] == 1
    assert older_url not in data
    assert old_url not in data
    assert data[newer_url] == record
    assert report["applied"][0]["removed_duplicate_catalog_urls"] == [older_url]


def test_does_not_remove_catalog_entries_with_different_metadata(tmp_path):
    old_url, older_url, newer_url, record, scan, comparison = make_case(tmp_path)
    path = tmp_path / "ps4/games.json"
    payload = json.loads(path.read_text())
    payload["DATA"][older_url] = {"name": "Different Game", "size": 999}
    path.write_text(json.dumps(payload))
    report = apply_verified_duplicates(scan, comparison, tmp_path)
    data = json.loads(path.read_text())["DATA"]
    assert report["applied_count"] == 0
    assert old_url in data
    assert older_url in data
    assert newer_url not in data


def test_rejects_group_without_matching_hash_verification(tmp_path):
    old_url, older_url, newer_url, record, scan, comparison = make_case(tmp_path)
    comparison["results"][1]["binary_verification"]["status"] = "unverified"
    report = apply_verified_duplicates(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert old_url in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_rejects_group_with_missing_or_timezone_naive_timestamp(tmp_path):
    old_url, older_url, newer_url, record, scan, comparison = make_case(tmp_path)
    scan["files"][1]["created_at"] = "2026-02-01T10:00:00"
    report = apply_verified_duplicates(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert old_url in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_rejects_selected_upload_that_is_not_newest(tmp_path):
    old_url, older_url, newer_url, record, scan, comparison = make_case(tmp_path)
    comparison["results"][1]["automatic_decision"]["selected_file_id"] = "older"
    report = apply_verified_duplicates(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert old_url in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]
