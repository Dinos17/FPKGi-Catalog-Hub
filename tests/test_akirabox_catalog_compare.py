import json
import sys
import pytest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import akirabox_catalog_compare as comparator
from akirabox_catalog_compare import compare_scan, parse_source_details, parse_source_identity, parse_source_metadata


@pytest.fixture(autouse=True)
def stub_catalog_link_checks(monkeypatch, request):
    if request.node.name.startswith("test_check_catalog_url_"):
        return
    monkeypatch.setattr(comparator, "check_catalog_url", lambda url: {
        "status": "reachable", "http_status": 200, "reason": "test URL reachable",
    })


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


def test_missing_version_or_size_is_not_claimed_matched(tmp_path):
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


def test_classifies_new_matched_and_outdated_without_writing(tmp_path):
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
    assert report["counts"] == {"new": 1, "matched": 1, "outdated": 1, "duplicate": 0, "unclassified": 0}
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

    assert report["results"][0]["status"] == "matched"
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

    assert [item["status"] for item in report["results"]] == ["matched", "matched"]
    assert report["counts"]["duplicate"] == 0


def test_duplicate_copy_suffix_normalizes_to_same_source_identity(tmp_path):
    assert parse_source_details("Absolute Drift - [US] [EN] [1.02] (1).pkg")[0] == "absolute drift"
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/absolute-drift.pkg": {
            "name": "Absolute Drift", "region": "USA", "version": "01.02", "size": 189136896,
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    scan = {"errors": [], "folders": [], "files": [
        {"id": "original", "name": "Absolute Drift - [US] [EN] [1.02].pkg", "platform": "PS4", "catalog_category": "games", "size_bytes": 189136896},
        {"id": "copy", "name": "Absolute Drift - [US] [EN] [1.02] (1).pkg", "platform": "PS4", "catalog_category": "games", "size_bytes": 189136896},
    ]}
    report = compare_scan(scan, tmp_path)
    assert report["counts"] == {"new": 0, "matched": 1, "outdated": 0, "duplicate": 1, "unclassified": 0}
    assert report["results"][1]["duplicate_of_file_id"] == "original"


def test_numbered_packages_sharing_title_id_are_not_duplicates(tmp_path):
    write_catalog(tmp_path, "ps4/dlc.json", {
        "https://archive.org/costume-pack-1.pkg": {
            "name": "Costume Pack (1)", "title_id": "CUSA12345",
            "region": "USA", "version": "01.00", "size": 100,
        },
    })
    for path in ("ps4/games.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    scan = {"errors": [], "folders": [], "files": [
        {"id": "pack-1", "name": "Costume.Pack.(1)_CUSA12345_v1.00_[5.05]-PS4.pkg",
         "platform": "PS4", "catalog_category": "dlc", "size_bytes": 100},
        {"id": "pack-2", "name": "Costume.Pack.(2)_CUSA12345_v1.00_[5.05]-PS4.pkg",
         "platform": "PS4", "catalog_category": "dlc", "size_bytes": 100},
    ]}
    report = compare_scan(scan, tmp_path)
    assert [item["status"] for item in report["results"]] == ["matched", "unclassified"]
    assert report["counts"]["duplicate"] == 0



def test_parses_language_and_firmware_without_confusing_version():
    parsed = parse_source_metadata(
        "[SuperPSX]-Slime.Rancher_CUSA11650_v1.00_[EN+FR]_[5.05]-PS4.pkg"
    )
    assert parsed["version"] == "1.00"
    assert parsed["languages"] == ["EN", "FR"]
    assert parsed["min_fw"] == "5.05"



def test_catalog_metadata_is_exposed_for_a_unique_match(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/example/game.pkg": {
            "name": "Example Game", "region": "USA", "version": "01.00",
            "size": 100, "release": "11-15-2013",
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "catalog-metadata",
        "name": "Example Game - [US] [EN] [1.00].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
    }]}, tmp_path)

    result = report["results"][0]
    assert result["status"] == "matched"
    assert result["catalog_metadata"]["name"] == "Example Game"
    assert result["catalog_metadata"]["version"] == "01.00"
    assert result["catalog_metadata"]["size"] == 100
    assert result["catalog_metadata"]["size_bytes"] == 100
    assert result["catalog_metadata"]["parsed_filename_metadata"]["title"] == "game"

def test_reports_only_source_observable_metadata_differences(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/game/Example_Game_CUSA12345_v1.00_[US]_[EN].pkg": {
            "name": "Example Game", "title_id": "CUSA12345", "region": "USA",
            "version": "01.00", "size": 100, "min_fw": "5.05",
            "release": "11-15-2013", "cover_url": "https://example.test/cover.png",
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "metadata-diff",
        "name": "Example_Game_CUSA12345_v1.00_[EU]_[FR].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
    }]}, tmp_path)
    result = report["results"][0]
    assert result["status"] == "matched"
    assert {item["field"] for item in result["metadata_differences"]} == {"region", "language"}
    assert "min_fw" not in {item["field"] for item in result["metadata_differences"]}
    assert "release" not in {item["field"] for item in result["metadata_differences"]}
    assert "cover_url" not in {item["field"] for item in result["metadata_differences"]}


def test_reports_minimum_firmware_difference_when_both_sides_have_it(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/game/Game_CUSA12345_v1.00_[5.05].pkg": {
            "name": "Example Game", "title_id": "CUSA12345", "region": "USA",
            "version": "01.00", "size": 100, "min_fw": "5.05",
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "firmware-diff",
        "name": "Example Game_CUSA12345_v1.00_[6.72].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
    }]}, tmp_path)
    assert report["results"][0]["metadata_differences"] == [{
        "field": "min_fw", "source_value": "6.72", "catalog_value": "5.05",
    }]


def test_source_duplicate_identity_includes_language_firmware_and_size(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/game.pkg": {
            "name": "Example Game", "title_id": "CUSA12345", "region": "USA",
            "version": "01.00", "size": 100, "min_fw": "5.05",
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    scan = {"errors": [], "folders": [], "files": [
        {"id": "en-505", "name": "Example Game_CUSA12345_v1.00_[US]_[EN]_[5.05].pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 100},
        {"id": "fr-505", "name": "Example Game_CUSA12345_v1.00_[US]_[FR]_[5.05].pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 100},
        {"id": "en-672", "name": "Example Game_CUSA12345_v1.00_[US]_[EN]_[6.72].pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 100},
        {"id": "en-different-size", "name": "Example Game_CUSA12345_v1.00_[US]_[EN]_[5.05].pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 101},
    ]}

    report = compare_scan(scan, tmp_path)

    assert [item["status"] for item in report["results"]] == [
        "matched", "matched", "matched", "outdated",
    ]
    assert report["counts"]["duplicate"] == 0


def test_single_dlc_title_id_candidate_still_requires_package_name_match(tmp_path):
    write_catalog(tmp_path, "ps4/dlc.json", {
        "https://archive.org/pack-1.pkg": {
            "name": "Rock Band Expansion Pack 1", "title_id": "CUSA02084",
            "region": "USA", "version": "01.00", "size": 100,
        },
    })
    for path in ("ps4/games.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "wrong-pack",
        "name": "Rock Band Expansion Pack 2_CUSA02084_v1.00_[US]_[EN].pkg",
        "platform": "PS4", "catalog_category": "dlc", "size_bytes": 100,
    }]}, tmp_path)

    assert report["results"][0]["status"] == "unclassified"
    assert "package name" in report["results"][0]["reason"]


def test_single_dlc_title_id_candidate_accepts_exact_package_name(tmp_path):
    write_catalog(tmp_path, "ps4/dlc.json", {
        "https://archive.org/pack-1.pkg": {
            "name": "Rock Band Expansion Pack 1", "title_id": "CUSA02084",
            "region": "USA", "version": "01.00", "size": 100,
        },
    })
    for path in ("ps4/games.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "right-pack",
        "name": "Rock Band Expansion Pack 1_CUSA02084_v1.00_[US]_[EN].pkg",
        "platform": "PS4", "catalog_category": "dlc", "size_bytes": 100,
    }]}, tmp_path)

    assert report["results"][0]["status"] == "matched"




def test_archive_org_catalog_entry_proposes_akirabox_replacement_without_writing(tmp_path):
    archive_url = "https://archive.org/download/example/game.pkg"
    share_url = "https://akirabox.to/abc123/file"
    write_catalog(tmp_path, "ps4/games.json", {
        archive_url: {
            "name": "Example Game", "region": "USA", "version": "01.00", "size": 100,
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "replacement", "name": "Example Game - [US] [EN] [1.00].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
        "share_url": share_url,
    }]}, tmp_path, link_checker=lambda url: {
        "status": "inconclusive", "http_status": 403, "reason": "HTTP 403",
    })
    result = report["results"][0]
    assert result["status"] == "outdated"
    assert result["catalog_url"] == archive_url
    assert result["proposed_replacement"] == {
        "from": archive_url, "to": share_url, "status": "pending_review",
    }
    assert report["replacement_candidate_count"] == 1
    assert report["catalog_write_performed"] is False
    assert json.loads((tmp_path / "ps4/games.json").read_text())["DATA"].keys() == {archive_url}


def test_archive_org_access_denial_does_not_block_replacement(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/example/game.pkg": {
            "name": "Example Game", "region": "USA", "version": "01.00", "size": 100,
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "denied", "name": "Example Game - [US] [EN] [1.00].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
        "share_url": "https://akirabox.to/denied/file",
    }]}, tmp_path, link_checker=lambda url: {
        "status": "inconclusive", "http_status": 403, "reason": "HTTP 403",
    })
    assert report["results"][0]["status"] == "outdated"
    assert report["results"][0]["proposed_replacement"]["to"] == "https://akirabox.to/denied/file"


class _StubResponse(comparator.requests.Response):
    def __init__(self, status, headers=None, forbid_body=False):
        super().__init__()
        self.status_code = status
        self.headers.update(headers or {})
        self._content = False
        self.raw = type("Raw", (), {"close": lambda self: None})()
        self.forbid_body = forbid_body

    @property
    def content(self):
        if self.forbid_body:
            raise AssertionError("redirect response body must not be consumed")
        return super().content


class _StubAdapter(comparator.requests.adapters.BaseAdapter):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return self.responses.pop(0)

    def close(self):
        pass


def _session_with_responses(*responses):
    session = comparator.requests.Session()
    adapter = _StubAdapter(responses)
    session.mount("https://", adapter)
    return session, adapter


def test_check_catalog_url_uses_head_and_classifies_404_without_downloading():
    session, adapter = _session_with_responses(_StubResponse(404))
    result = comparator.check_catalog_url("https://archive.org/download/example/game.pkg", session)
    assert result["status"] == "broken"
    assert result["http_status"] == 404
    assert [call[0].method for call in adapter.calls] == ["HEAD"]
    assert adapter.calls[0][1]["stream"] is True


def test_check_catalog_url_falls_back_to_one_byte_range_when_head_unsupported():
    session, adapter = _session_with_responses(_StubResponse(405), _StubResponse(206))
    result = comparator.check_catalog_url("https://archive.org/download/example/game.pkg", session)
    assert result["status"] == "reachable"
    assert [call[0].method for call in adapter.calls] == ["HEAD", "GET"]
    assert adapter.calls[1][0].headers["Range"] == "bytes=0-0"
    assert adapter.calls[1][1]["stream"] is True


def test_check_catalog_url_rejects_non_archive_hosts_without_request():
    session, adapter = _session_with_responses()
    result = comparator.check_catalog_url("https://example.com/game.pkg", session)
    assert result["status"] == "inconclusive"
    assert adapter.calls == []


def test_check_catalog_url_rejects_backslash_authority_bypass():
    session, adapter = _session_with_responses()
    result = comparator.check_catalog_url(
        r"https://example.com\\.archive.org/game.pkg", session
    )
    assert result["status"] == "inconclusive"
    assert adapter.calls == []


def test_check_catalog_url_does_not_consume_malformed_redirect_body():
    session, adapter = _session_with_responses(
        _StubResponse(302, {"Location": "https://["}, forbid_body=True)
    )
    result = comparator.check_catalog_url("https://archive.org/download/example/game.pkg", session)
    assert result["status"] == "inconclusive"
    assert "Malformed redirect URL" in result["reason"]
    assert len(adapter.calls) == 1


def test_akirabox_file_endpoint_is_not_misreported_as_catalog_filename(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://akirabox.to/example123/file": {
            "name": "Example Game", "region": "USA", "version": "01.00", "size": 100,
        },
    })
    for path in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    report = compare_scan({"errors": [], "folders": [], "files": [{
        "id": "akirabox-endpoint",
        "name": "Example Game - [US] [EN] [1.00].pkg",
        "platform": "PS4", "catalog_category": "games", "size_bytes": 100,
    }]}, tmp_path)

    result = report["results"][0]
    assert result["status"] == "matched"
    assert "parsed_filename_metadata" not in result["catalog_metadata"]


class _AkiraPageResponse:
    def __init__(self, status=200, body="", content_type="text/html; charset=utf-8"):
        self.status_code = status
        self.text = body
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def close(self):
        self.closed = True


class _AkiraPageSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_fetch_akirabox_sha256_reads_escaped_page_field_without_following_redirects():
    digest = "c2e0dea5adf67cafdb03ae3eb6f0a708def8570f423aeed09e0f7696032fb273"
    response = _AkiraPageResponse(body=(
        r'\"sha256\":{\"value\":\"' + digest + r'\",\"pending\":false}'
    ))
    session = _AkiraPageSession(response)

    result = comparator.fetch_akirabox_sha256("jJD3V1vYlma5", session)

    assert result == {
        "status": "available", "sha256": digest,
        "reason": "SHA-256 read from AkiraBox file page",
    }
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["stream"] is True
    assert session.calls[0][1]["timeout"] == comparator.AKIRABOX_HASH_TIMEOUT_SECONDS
    assert response.closed is True


def test_fetch_akirabox_sha256_treats_null_as_unavailable():
    response = _AkiraPageResponse(body=r'\"sha256\":{\"value\":null,\"pending\":false}')
    result = comparator.fetch_akirabox_sha256("APVma1BW6mXo", _AkiraPageSession(response))
    assert result["status"] == "unavailable"
    assert result["sha256"] is None


def test_fetch_akirabox_sha256_does_not_follow_redirects_or_read_binary():
    response = _AkiraPageResponse(status=302, body="", content_type="application/octet-stream")
    session = _AkiraPageSession(response)
    result = comparator.fetch_akirabox_sha256("abc123", session)
    assert result["status"] == "unavailable"
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize(
    ("first_hash", "second_hash", "expected"),
    [
        ("a" * 64, "a" * 64, "verified_identical"),
        ("a" * 64, "b" * 64, "different"),
        (None, "b" * 64, "unverified"),
    ],
)
def test_source_duplicate_binary_verification_uses_sha256_when_available(
    tmp_path, first_hash, second_hash, expected
):
    for path in ("ps4/games.json", "ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, path, {})
    values = {
        "first": {"status": "available", "sha256": first_hash, "reason": "test"},
        "second": {
            "status": "available" if second_hash else "unavailable",
            "sha256": second_hash, "reason": "test",
        },
    }
    calls = []

    def hash_checker(file_id):
        calls.append(file_id)
        return values[file_id]

    scan = {"errors": [], "folders": [], "files": [
        {"id": "first", "name": "Absolute Drift - [US] [EN] [1.02].pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 189136896},
        {"id": "second", "name": "Absolute Drift - [US] [EN] [1.02] (1).pkg",
         "platform": "PS4", "catalog_category": "games", "size_bytes": 189136896},
    ]}

    report = compare_scan(scan, tmp_path, hash_checker=hash_checker)

    assert report["results"][1]["status"] == "duplicate"
    assert report["results"][1]["binary_verification"]["status"] == expected
    assert calls == ["first", "second"]
