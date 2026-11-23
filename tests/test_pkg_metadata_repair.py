import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pkg_metadata_repair as repair
import pkg_remote_metadata as pkg


SOURCE = "https://archive.org/download/rb4/Rock%20Band%204%20Expansion%20Pack%201.pkg?download=true"
FILE_ID = "123e4567-e89b-12d3-a456-426614174000"


def make_root(tmp_path, *, metadata=None, ps5_metadata=None):
    metadata = metadata or {
        "title_id": "CUSA02084",
        "region": "USA",
        "name": "Rock Band 4 Expansion Pack 1",
        "size": 123456,
        "min_fw": "9.00",
        "cover_url": "CUSA02084.png",
    }
    ps5_metadata = ps5_metadata or {}
    (tmp_path / "ps4").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ps5").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ps4" / "dlc.json").write_text(
        json.dumps({"DATA": {SOURCE: metadata}}, indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "ps5" / "ps5-apps.json").write_text(
        json.dumps({"DATA": ps5_metadata}, indent=2) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def make_scan(*files):
    return {
        "scanner": "mocha",
        "scan_complete": True,
        "read_only": True,
        "catalog_write_performed": False,
        "package_binaries_downloaded": False,
        "file_count": len(files),
        "files": list(files),
    }


def mocha_file(*, file_id=FILE_ID, name="Rock Band 4 Expansion Pack 1.pkg", size=123456,
               folder="ps4/dlc"):
    return {
        "id": file_id,
        "name": name,
        "size_bytes": size,
        "folder_path": folder,
        "extension": "pkg",
    }


def extracted(target, *, title_id="CUSA02084", version="01.02", size=123456, region=None):
    return {
        "status": "extracted",
        "title_id": title_id,
        "version": version,
        "region": region,
        "package_size": size,
        "category": "ac",
    }


def read_dlc(root):
    return json.loads((root / "ps4" / "dlc.json").read_text(encoding="utf-8"))["DATA"][SOURCE]


def test_exact_mocha_filename_and_size_selects_unique_mocha_file(tmp_path):
    root = make_root(tmp_path)
    seen = {}

    def inspector(target):
        seen["source"] = target["source"]
        return extracted(target)

    report = repair.repair_catalogs(
        root, make_scan(mocha_file()), inspector=inspector
    )
    result = report["results"][0]
    assert seen["source"] == FILE_ID
    assert result["mocha_match_status"] == "unique_exact_match"
    assert result["mocha_match"]["file_id"] == FILE_ID
    assert result["status"] == "candidate_update"
    assert result["proposed_updates"] == {"version": "01.02"}
    assert result["source"] == "https://archive.org/download/rb4/Rock%20Band%204%20Expansion%20Pack%201.pkg"
    assert "download=true" not in json.dumps(report)
    assert "version" not in read_dlc(root)
    assert report["read_only"] is True
    assert report["proposed_count"] == 1
    assert report["applied_count"] == 0


def test_apply_updates_only_verified_missing_version(tmp_path):
    root = make_root(tmp_path)
    before = read_dlc(root).copy()
    report = repair.repair_catalogs(
        root, make_scan(mocha_file()), apply=True,
        inspector=lambda target: extracted(target),
    )
    after = read_dlc(root)
    assert after["version"] == "01.02"
    for key, value in before.items():
        assert after[key] == value
    assert report["read_only"] is False
    assert report["catalog_files_modified"] is True
    assert report["applied_count"] == 1
    assert report["results"][0]["status"] == "updated"


@pytest.mark.parametrize(
    ("title_id", "size"),
    [("CUSA99999", 123456), ("CUSA02084", 999999)],
)
def test_identity_mismatch_never_changes_catalog(tmp_path, title_id, size):
    root = make_root(tmp_path)
    before = (root / "ps4" / "dlc.json").read_text(encoding="utf-8")
    report = repair.repair_catalogs(
        root, make_scan(mocha_file()), apply=True,
        inspector=lambda target: extracted(target, title_id=title_id, size=size),
    )
    assert report["applied_count"] == 0
    assert report["results"][0]["status"] == "identity_mismatch"
    assert (root / "ps4" / "dlc.json").read_text(encoding="utf-8") == before


def test_mismatched_mocha_inventory_does_not_select_candidate(tmp_path):
    root = make_root(tmp_path)
    seen = {}

    def inspector(target):
        seen["source"] = target["source"]
        raise pkg.MetadataError("HTTP 401")

    report = repair.repair_catalogs(
        root, make_scan(mocha_file(size=123457)), inspector=inspector
    )
    result = report["results"][0]
    assert seen["source"] == SOURCE
    assert result["mocha_match_status"] == "not_found"
    assert result["status"] == "unavailable"


def test_multiple_exact_mocha_matches_are_ambiguous_and_not_used(tmp_path):
    root = make_root(tmp_path)
    seen = {}

    def inspector(target):
        seen["source"] = target["source"]
        raise pkg.MetadataError("HTTP 401")

    report = repair.repair_catalogs(
        root,
        make_scan(
            mocha_file(file_id=FILE_ID),
            mocha_file(file_id="223e4567-e89b-12d3-a456-426614174000"),
        ),
        inspector=inspector,
    )
    result = report["results"][0]
    assert seen["source"] == SOURCE
    assert result["mocha_match_status"] == "ambiguous_exact_matches"
    assert result["status"] == "unavailable"


def test_version_can_be_repaired_without_guessing_missing_region(tmp_path):
    metadata = {
        "title_id": "CUSA02084",
        "name": "Rock Band 4 Expansion Pack 1",
        "size": 123456,
    }
    root = make_root(tmp_path, metadata=metadata)
    report = repair.repair_catalogs(
        root, make_scan(mocha_file()), apply=True,
        inspector=lambda target: extracted(target, region=None),
    )
    after = read_dlc(root)
    assert after["version"] == "01.02"
    assert "region" not in after
    assert report["results"][0]["unresolved_fields"] == ["region"]
    assert "Region is not reliably available" in report["results"][0]["reason"]


def test_invalid_extracted_version_is_not_applied(tmp_path):
    root = make_root(tmp_path)
    report = repair.repair_catalogs(
        root, make_scan(mocha_file()), apply=True,
        inspector=lambda target: extracted(target, version="unknown"),
    )
    assert report["applied_count"] == 0
    assert report["results"][0]["status"] == "extracted_no_safe_update"
    assert "version" not in read_dlc(root)


def test_missing_catalog_title_id_prevents_automatic_repair(tmp_path):
    metadata = {"name": "Rock Band 4 Expansion Pack 1", "region": "USA", "size": 123456}
    root = make_root(tmp_path, metadata=metadata)
    report = repair.repair_catalogs(
        root, make_scan(mocha_file()), apply=True,
        inspector=lambda target: extracted(target),
    )
    assert report["applied_count"] == 0
    assert report["results"][0]["status"] == "identity_mismatch"
    assert "title_id is missing" in report["results"][0]["reason"]


def test_ps5_targets_remain_unsupported_and_unchanged(tmp_path):
    ps5_source = "https://huggingface.co/datasets/example/file.pkg"
    root = make_root(tmp_path, metadata={
        "title_id": "CUSA00001", "region": "USA", "name": "Complete PS4 entry",
        "size": 123456, "version": "01.00",
    }, ps5_metadata={
        ps5_source: {"title_id": "PPSA01615", "name": "Example", "size": 456, "version": ""}
    })
    before = (root / "ps5" / "ps5-apps.json").read_text(encoding="utf-8")
    report = repair.repair_catalogs(root, make_scan(), apply=True)
    result = next(row for row in report["results"] if row["catalog"] == "ps5/ps5-apps.json")
    assert result["status"] == "unsupported_platform"
    assert (root / "ps5" / "ps5-apps.json").read_text(encoding="utf-8") == before


def test_rejects_incomplete_or_mutating_scan_reports(tmp_path):
    path = tmp_path / "scan.json"
    path.write_text(json.dumps({
        "scanner": "mocha", "scan_complete": False, "read_only": True,
        "catalog_write_performed": False, "package_binaries_downloaded": False,
        "file_count": 0, "files": [],
    }), encoding="utf-8")
    with pytest.raises(repair.RepairError, match="incomplete"):
        repair.load_scan_report(path)


def test_rejects_scan_file_count_mismatch(tmp_path):
    path = tmp_path / "scan.json"
    path.write_text(json.dumps({
        "scanner": "mocha", "scan_complete": True, "read_only": True,
        "catalog_write_performed": False, "package_binaries_downloaded": False,
        "file_count": 1, "files": [],
    }), encoding="utf-8")
    with pytest.raises(repair.RepairError, match="file count"):
        repair.load_scan_report(path)


def test_existing_mocha_id_is_used_without_inventory_name_match(tmp_path):
    root = make_root(tmp_path)
    metadata = read_dlc(root)
    document_path = root / "ps4" / "dlc.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    document["DATA"] = {FILE_ID: metadata}
    document_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    seen = {}

    def inspector(target):
        seen["source"] = target["source"]
        return extracted(target)

    report = repair.repair_catalogs(root, make_scan(), inspector=inspector)
    assert seen["source"] == FILE_ID
    assert report["results"][0]["mocha_match_status"] == "catalog_mocha_id"
    assert report["results"][0]["status"] == "candidate_update"
