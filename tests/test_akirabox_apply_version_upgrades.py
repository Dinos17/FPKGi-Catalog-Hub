import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from akirabox_apply_version_upgrades import apply_version_upgrades


OLD_URL = "https://akirabox.to/old123/file"
NEW_URL = "https://akirabox.to/new456/file"


def write_catalog(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def make_case(tmp_path):
    record = {
        "name": "Example Game", "title_id": "CUSA12345", "region": "USA",
        "version": "01.00", "size": 100, "min_fw": "9.00",
        "cover_url": "https://example.test/cover.png",
    }
    write_catalog(tmp_path, "ps4/games.json", {OLD_URL: record})
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    scan = {"files": [{
        "id": "file-1", "name": "Example Game_CUSA12345_v1.05_[US]_[EN].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 120,
        "share_url": NEW_URL,
    }]}
    comparison = {"results": [{
        "status": "outdated", "file_id": "file-1", "name": scan["files"][0]["name"],
        "platform": "PS4", "category": "games", "catalog_url": OLD_URL,
        "metadata_differences": [
            {"field": "version", "source_value": "1.05", "catalog_value": "01.00"},
            {"field": "size_bytes", "source_value": 120, "catalog_value": 100},
        ],
        "source_metadata": {
            "title": "example game", "title_id": "CUSA12345", "region": "USA",
            "version": "1.05", "size_bytes": 120,
        },
        "catalog_metadata": {**record, "size_bytes": 100},
    }]}
    return scan, comparison


def test_updates_version_url_and_package_size_together(tmp_path):
    scan, comparison = make_case(tmp_path)
    report = apply_version_upgrades(scan, comparison, tmp_path)
    data = json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]
    assert report["applied_count"] == 1
    assert OLD_URL not in data
    assert data[NEW_URL]["version"] == "1.05"
    assert data[NEW_URL]["size"] == 120
    assert data[NEW_URL]["title_id"] == "CUSA12345"
    assert data[NEW_URL]["cover_url"] == "https://example.test/cover.png"


def test_rejects_same_or_older_version(tmp_path):
    scan, comparison = make_case(tmp_path)
    comparison["results"][0]["source_metadata"]["version"] = "1.00"
    report = apply_version_upgrades(scan, comparison, tmp_path)
    data = json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]
    assert report["applied_count"] == 0
    assert OLD_URL in data


def test_rejects_title_id_mismatch(tmp_path):
    scan, comparison = make_case(tmp_path)
    comparison["results"][0]["source_metadata"]["title_id"] = "CUSA99999"
    report = apply_version_upgrades(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert OLD_URL in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_rejects_region_or_other_metadata_mismatch(tmp_path):
    scan, comparison = make_case(tmp_path)
    comparison["results"][0]["metadata_differences"].append(
        {"field": "region", "source_value": "EUR", "catalog_value": "USA"}
    )
    report = apply_version_upgrades(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert OLD_URL in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_rejects_ambiguous_multiple_upgrade_candidates(tmp_path):
    scan, comparison = make_case(tmp_path)
    second = dict(scan["files"][0], id="file-2")
    scan["files"].append(second)
    second_result = dict(comparison["results"][0], file_id="file-2")
    comparison["results"].append(second_result)
    report = apply_version_upgrades(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert OLD_URL in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_rejects_existing_destination_url(tmp_path):
    scan, comparison = make_case(tmp_path)
    path = tmp_path / "ps4/games.json"
    payload = json.loads(path.read_text())
    payload["DATA"][NEW_URL] = {"name": "Do not overwrite"}
    path.write_text(json.dumps(payload))
    report = apply_version_upgrades(scan, comparison, tmp_path)
    data = json.loads(path.read_text())["DATA"]
    assert report["applied_count"] == 0
    assert data[NEW_URL]["name"] == "Do not overwrite"
    assert OLD_URL in data


def test_rejects_stale_catalog_record(tmp_path):
    scan, comparison = make_case(tmp_path)
    path = tmp_path / "ps4/games.json"
    payload = json.loads(path.read_text())
    payload["DATA"][OLD_URL]["version"] = "01.02"
    path.write_text(json.dumps(payload))
    report = apply_version_upgrades(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert OLD_URL in json.loads(path.read_text())["DATA"]


def test_rejects_catalog_field_added_after_comparison_snapshot(tmp_path):
    scan, comparison = make_case(tmp_path)
    del comparison["results"][0]["catalog_metadata"]["min_fw"]
    report = apply_version_upgrades(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert OLD_URL in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_uses_comparator_unicode_title_normalization(tmp_path):
    scan, comparison = make_case(tmp_path)
    path = tmp_path / "ps4/games.json"
    payload = json.loads(path.read_text())
    payload["DATA"][OLD_URL]["name"] = "Café Game"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    comparison["results"][0]["catalog_metadata"]["name"] = "Café Game"
    comparison["results"][0]["source_metadata"]["title"] = "cafe game"
    scan["files"][0]["name"] = "Cafe Game_CUSA12345_v1.05_[US]_[EN].pkg"
    report = apply_version_upgrades(scan, comparison, tmp_path)
    data = json.loads(path.read_text())["DATA"]
    assert report["applied_count"] == 1
    assert data[NEW_URL]["version"] == "1.05"


def test_rejects_valid_upgrade_when_other_candidate_fails_eligibility(tmp_path):
    scan, comparison = make_case(tmp_path)
    second = dict(scan["files"][0], id="file-2", name="Different Game_CUSA99999_v1.05_[US]_[EN].pkg")
    scan["files"].append(second)
    second_result = dict(comparison["results"][0])
    second_result.update({
        "file_id": "file-2",
        "source_metadata": dict(comparison["results"][0]["source_metadata"], title_id="CUSA99999"),
    })
    comparison["results"].append(second_result)
    report = apply_version_upgrades(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert OLD_URL in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]


def test_duplicate_source_identity_counts_as_ambiguous_target(tmp_path):
    scan, comparison = make_case(tmp_path)
    second = dict(scan["files"][0], id="file-2")
    scan["files"].append(second)
    comparison["results"].append({
        "status": "duplicate",
        "file_id": "file-2",
        "duplicate_of_file_id": "file-1",
    })
    report = apply_version_upgrades(scan, comparison, tmp_path)
    assert report["applied_count"] == 0
    assert OLD_URL in json.loads((tmp_path / "ps4/games.json").read_text())["DATA"]
