from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

from tools.mocha_scanner import MochaScanner, ScanBudgetReached, ScanError
from tools.mocha_catalog_compare import compare_scan
from tools.mocha_review_queue import can_silently_clear_match, near_duplicate_file_ids


class FakeResponse:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=1):
        raw = json.dumps(self.payload).encode("utf-8")
        for offset in range(0, len(raw), chunk_size):
            yield raw[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class SlowResponse(FakeResponse):
    def iter_content(self, chunk_size=1):
        time.sleep(0.02)
        yield b"{}"


class FakeSession:
    def __init__(self, responses, expected_paths=None):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []
        self.expected_paths = list(expected_paths or [])

    def get(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        self.calls.append((url, kwargs))
        if url.endswith("/files") and self.expected_paths:
            expected = self.expected_paths.pop(0)
            assert kwargs["params"]["path"] == expected
        return self.responses.pop(0)


def payload(files=None, folders=None, *, has_more=False, cursor=None):
    return {
        "files": files or [],
        "folders": folders or [],
        "hasMore": has_more,
        "nextCursor": cursor,
    }


def file_row(file_id="f1", name="Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
             path="PS4/Games", size=1234, **extra):
    return {
        "id": file_id,
        "original_name": name,
        "size": size,
        "path": path,
        **extra,
    }


def test_scanner_recursively_reads_expected_folder_paths_and_classifies():
    session = FakeSession([
        FakeResponse({"canUseApi": True, "canCreateHotlinks": False}),
        FakeResponse({"canUseApi": True, "canCreateHotlinks": False}),
        FakeResponse(payload(folders=["PS4"])),
        FakeResponse(payload(folders=["Games"])),
        FakeResponse(payload(files=[file_row()])),
    ], expected_paths=["", "PS4", "PS4/Games"])

    report = MochaScanner("test-key", session, max_seconds=10).run()

    assert report["scan_complete"] is True
    assert report["folder_count"] == 3
    assert report["file_count"] == 1
    assert report["permissions"]["can_create_hotlinks"] is False
    item = report["files"][0]
    assert item["platform"] == "PS4"
    assert item["catalog_category"] == "games"
    assert item["sha256"] is None
    assert item["download_url"] is None
    assert item["hotlink_available"] is False
    assert report["catalog_write_performed"] is False
    assert report["mocha_files_deleted"] is False
    assert session.expected_paths == []


def test_scanner_uses_cursor_pagination_and_keeps_distinct_files():
    session = FakeSession([
        FakeResponse({}),
        FakeResponse({}),
        FakeResponse(payload(files=[file_row("f1")], has_more=True, cursor="next")),
        FakeResponse(payload(files=[file_row("f2", name="Another_Game_CUSA54321_v1.00_[US]_[EN].pkg")],
                             has_more=False)),
    ], expected_paths=["", ""])
    report = MochaScanner("test-key", session, max_seconds=10).run()

    assert report["listing_page_count"] == 2
    assert report["file_count"] == 2
    assert {item["id"] for item in report["files"]} == {"f1", "f2"}
    assert session.calls[2][1]["params"]["path"] == ""
    assert session.calls[3][1]["params"]["cursor"] == "next"


def test_scanner_skips_queued_duplicate_folder_at_folder_limit():
    session = FakeSession([
        FakeResponse({}),
        FakeResponse({}),
        FakeResponse(payload(folders=["PS4", "PS4"])),
        FakeResponse(payload()),
    ], expected_paths=["", "PS4"])
    report = MochaScanner("test-key", session, max_folders=2, max_seconds=10).run()
    assert report["scan_complete"] is True
    assert report["folder_count"] == 2


def test_scanner_preserves_whitespace_in_folder_names():
    assert MochaScanner.child_path("PS4", " Games ") == "PS4/ Games "


def test_scanner_records_timezone_aware_inventory_observation_time():
    session = FakeSession([
        FakeResponse({}),
        FakeResponse({}),
        FakeResponse(payload()),
    ], expected_paths=[""])
    report = MochaScanner("test-key", session, max_seconds=10).run()

    observed_at = datetime.fromisoformat(report["scan_observed_at"].replace("Z", "+00:00"))
    assert observed_at.tzinfo is not None


def test_scanner_rejects_redirects_instead_of_forwarding_credentials():
    session = FakeSession([FakeResponse(status=302, headers={"Location": "https://evil.example/"})])
    scanner = MochaScanner("test-key", session, max_seconds=10)
    with pytest.raises(ScanError, match="redirected"):
        scanner.client.get_json("/auth/me", deadline=time.monotonic() + 10)


def test_scanner_retries_transient_server_errors():
    session = FakeSession([FakeResponse(status=503), FakeResponse({"canUseApi": True})])
    scanner = MochaScanner("test-key", session, max_seconds=10)
    assert scanner.client.get_json("/auth/me", deadline=time.monotonic() + 10) == {"canUseApi": True}
    assert len(session.calls) == 2


def test_scanner_retries_http_408():
    session = FakeSession([FakeResponse(status=408), FakeResponse({"canUseApi": True})])
    scanner = MochaScanner("test-key", session, max_seconds=10)
    assert scanner.client.get_json("/auth/me", deadline=time.monotonic() + 10) == {"canUseApi": True}
    assert len(session.calls) == 2


def test_scanner_rejects_oversized_response():
    class OversizedResponse(FakeResponse):
        def iter_content(self, chunk_size=1):
            yield b"x" * (10 * 1024 * 1024 + 1)

    session = FakeSession([OversizedResponse({})])
    scanner = MochaScanner("test-key", session, max_seconds=10)
    with pytest.raises(ScanError, match="maximum allowed size"):
        scanner.client.get_json("/auth/me", deadline=time.monotonic() + 10)


def test_optional_profile_permission_error_does_not_block_file_scan():
    session = FakeSession([
        FakeResponse({"canUseApi": True, "canCreateHotlinks": False}),
        FakeResponse(status=403),
        FakeResponse(payload()),
    ], expected_paths=[""])
    report = MochaScanner("test-key", session, max_seconds=10).run()
    assert report["scan_complete"] is True
    assert report["account_summary_available"] is True
    assert report["permissions"]["profile_can_use_api"] is None


def test_scanner_retries_expired_http_date_immediately():
    expired = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=5), usegmt=True)
    session = FakeSession([
        FakeResponse(status=429, headers={"Retry-After": expired}),
        FakeResponse({"canUseApi": True}),
    ])
    scanner = MochaScanner("test-key", session, max_seconds=10)
    assert scanner.client.get_json("/auth/me", deadline=time.monotonic() + 10) == {"canUseApi": True}
    assert len(session.calls) == 2


def test_scanner_enforces_deadline_while_reading_response():
    session = FakeSession([SlowResponse({})])
    scanner = MochaScanner("test-key", session, max_seconds=10)
    with pytest.raises(ScanBudgetReached, match="while reading"):
        scanner.client.get_json("/auth/me", deadline=time.monotonic() + 0.005)


def test_scanner_rejects_repeated_pagination_cursor():
    session = FakeSession([
        FakeResponse({}),
        FakeResponse({}),
        FakeResponse(payload(has_more=True, cursor="repeat")),
        FakeResponse(payload(has_more=True, cursor="repeat")),
    ])
    scanner = MochaScanner("test-key", session, max_seconds=10)
    with pytest.raises(ScanError, match="cursor repeated"):
        scanner.run()


def write_catalog(root: Path, relative: str, data: dict):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def write_empty_catalogs(root: Path):
    for relative in ("ps4/games.json", "ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(root, relative, {})


def make_complete_scan(files):
    return {
        "scanner": "mocha",
        "scan_complete": True,
        "catalog_write_performed": False,
        "snapshot_consistency": "not_guaranteed_by_api",
        "permissions": {"can_create_hotlinks": False},
        "files": files,
    }


def test_comparison_matches_exact_catalog_entry_and_rejects_wrong_title(tmp_path):
    catalog_url = (
        "https://archive.org/download/example/"
        "Example_Game_CUSA12345_v1.00_[US]_[EN].pkg"
    )
    write_catalog(tmp_path, "ps4/games.json", {
        catalog_url: {
            "name": "Example Game", "title_id": "CUSA12345",
            "region": "USA", "version": "01.00", "size": 1234, "min_fw": "9.00",
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})

    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    report = compare_scan(make_complete_scan([{
        "id": "f1", "name": "Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    after = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}

    assert report["counts"]["matched"] == 1
    assert report["results"][0]["status"] == "matched"
    assert report["results"][0]["catalog_url"] == catalog_url
    assert before == after

    wrong_title = compare_scan(make_complete_scan([{
        "id": "f2", "name": "Completely_Different_Game_CUSA12345_v1.00_[US]_[EN].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert wrong_title["counts"]["matched"] == 0
    assert wrong_title["results"][0]["status"] == "new"


def test_different_dlc_titles_with_shared_title_id_are_not_grouped(tmp_path):
    write_empty_catalogs(tmp_path)
    common = {
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "dlc", "folder_path": "PS4/DLC",
    }
    scan = make_complete_scan([
        {"id": "dlc-a", "name": "Example_Game_Expansion_A_CUSA12345_v1.00_[US]_[EN].pkg", **common},
        {"id": "dlc-b", "name": "Example_Game_Expansion_B_CUSA12345_v1.00_[US]_[EN].pkg", **common},
    ])
    report = compare_scan(scan, tmp_path)
    assert report["possible_duplicates"] == []


def test_metadata_differences_include_firmware_and_languages(tmp_path):
    url = "https://archive.org/download/example/Example_Game_CUSA12345_v1.00_[US]_[FR]_[8.00].pkg"
    write_catalog(tmp_path, "ps4/games.json", {
        url: {"name": "Example Game", "title_id": "CUSA12345", "region": "USA",
              "version": "01.00", "size": 1234, "min_fw": "7.00"},
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "f1", "name": "Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert report["results"][0]["status"] == "metadata_difference"
    assert set(report["results"][0]["metadata_differences"]) == {"min_fw", "languages"}


def test_missing_optional_mocha_metadata_does_not_block_strong_exact_match(tmp_path):
    # Mocha omits title ID and minimum firmware. A unique exact title candidate
    # with matching region, version, and exact package size is still verifiable.
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/example.pkg": {
            "name": "Example Game", "title_id": "CUSA12345",
            "region": "USA", "version": "01.00", "size": 1234, "min_fw": "9.00",
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "mocha-no-optional-fields",
        "name": "Example_Game_v1.00_[US]_[EN].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)

    row = report["results"][0]
    assert row["status"] == "matched"
    assert set(row["unavailable_metadata"]) == {"title_id", "min_fw", "languages"}
    assert "optional metadata unavailable" in row["reason"]



def test_catalog_filename_title_id_conflict_is_detected(tmp_path):
    url = "https://archive.org/download/example/Example_Game_CUSA99999_v1.00_[US]_[EN]_[9.00].pkg"
    write_catalog(tmp_path, "ps4/games.json", {
        url: {"name": "Example Game", "region": "USA", "version": "1.00",
              "size": 1234, "min_fw": "9.00"},
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "filename-id-conflict",
        "name": "Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    row = report["results"][0]
    assert row["status"] == "metadata_difference"
    assert "title_id" in row["metadata_differences"]


def test_catalog_metadata_languages_are_used_when_filename_has_no_tags(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/example/file": {
            "name": "Example Game", "title_id": "CUSA12345",
            "region": "USA", "version": "1.00", "size": 1234,
            "languages": ["FR"],
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "language-conflict",
        "name": "Example_Game_CUSA12345_v1.00_[US]_[EN].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    row = report["results"][0]
    assert row["status"] == "metadata_difference"
    assert "languages" in row["metadata_differences"]


def test_reordered_title_words_do_not_verify_identity(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/example.pkg": {
            "name": "Example Game", "title_id": "CUSA12345",
            "region": "USA", "version": "1.00", "size": 1234, "min_fw": "9.00",
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "reordered-title",
        "name": "Game_Example_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    row = report["results"][0]
    assert row["status"] == "unverified"
    assert "package_title_identity" in row["unavailable_metadata"]


def test_metadata_match_is_not_binary_approval(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/example.pkg": {
            "name": "Example Game", "title_id": "CUSA12345",
            "region": "USA", "version": "1.00", "size": 1234, "min_fw": "9.00",
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "metadata-only",
        "name": "Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    row = report["results"][0]
    assert row["status"] == "matched"
    assert row["binary_integrity_verified"] is False
    assert "binary contents are not cryptographically verified" in row["reason"]


def test_unique_metadata_matches_clear_without_claiming_binary_verification():
    # Metadata-based catalog classification is independent from binary integrity.
    assert can_silently_clear_match("matched", False, False, "stable") is True
    assert can_silently_clear_match("matched", False, None, "stable") is True
    assert can_silently_clear_match("matched", False, True, "stable") is True
    assert can_silently_clear_match("matched", False, False, "not_guaranteed_by_api") is False
    assert can_silently_clear_match("matched", True, False, "stable") is False
    assert can_silently_clear_match("metadata_difference", False, False, "stable") is False
    assert can_silently_clear_match("unverified", False, False, "stable") is False
    assert can_silently_clear_match("new", False, False, "stable") is False


def test_near_duplicate_matches_with_different_optional_tags_stay_flagged():
    rows = [
        {
            "file_id": "one", "status": "matched", "platform": "PS4", "category": "games",
            "source_metadata": {
                "title": "Example Game", "title_id": "CUSA12345", "region": "USA",
                "version": "1.00", "size_bytes": 1234, "languages": ["EN"], "min_fw": "5.05",
            },
        },
        {
            "file_id": "two", "status": "metadata_difference", "platform": "PS4", "category": "games",
            "source_metadata": {
                "title": "Example Game", "title_id": "CUSA12345", "region": "USA",
                "version": "1.00", "size_bytes": 1234, "languages": ["EN", "FR"], "min_fw": "6.72",
            },
        },
        {
            "file_id": "three", "status": "matched", "platform": "PS4", "category": "games",
            "source_metadata": {
                "title": "Different Game", "title_id": "CUSA99999", "region": "USA",
                "version": "1.00", "size_bytes": 1234,
            },
        },
    ]
    assert near_duplicate_file_ids(rows) == {"one", "two"}


def test_missing_comparison_critical_metadata_is_unverified(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/example.pkg": {
            "name": "Example Game", "title_id": "CUSA12345",
            "region": "USA", "version": "", "size": 1234,
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "f1", "name": "Example_Game_CUSA12345_v1.00_[US].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert report["results"][0]["status"] == "unverified"
    assert "version" in report["results"][0]["unavailable_metadata"]


def test_unbracketed_version_suffix_does_not_prevent_title_match(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/example.pkg": {
            "name": "Example Game", "region": "USA", "version": "1.00", "size": 1234,
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "f1", "name": "Example Game_v1.00.pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert report["results"][0]["status"] == "unverified"
    assert report["results"][0]["catalog_url"] == "https://archive.org/example.pkg"


def test_title_match_falls_back_when_title_id_is_absent_from_catalog(tmp_path):
    url = "https://archive.org/download/example/Example_Game_CUSA99999_v1.00_[US]_[EN]_[9.00].pkg"
    write_catalog(tmp_path, "ps4/games.json", {
        url: {"name": "Example Game", "title_id": "CUSA99999", "region": "USA",
              "version": "1.00", "size": 1234, "min_fw": "9.00"},
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "fallback", "name": "Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert report["results"][0]["catalog_url"] == url
    assert report["results"][0]["status"] == "metadata_difference"
    assert "title_id" in report["results"][0]["metadata_differences"]


def test_similar_package_title_is_not_reported_as_a_match(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/example.pkg": {
            "name": "Example Game Deluxe", "title_id": "CUSA12345",
            "region": "USA", "version": "1.00", "size": 1234, "min_fw": "9.00",
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "similar", "name": "Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert report["results"][0]["status"] == "unverified"


def test_source_region_disambiguates_same_title_id_catalog_entries(tmp_path):
    us_url = "https://archive.org/us/Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg"
    eu_url = "https://archive.org/eu/Example_Game_CUSA12345_v1.00_[EU]_[EN]_[9.00].pkg"
    write_catalog(tmp_path, "ps4/games.json", {
        us_url: {"name": "Example Game", "title_id": "CUSA12345", "region": "USA",
                 "version": "1.00", "size": 1234, "min_fw": "9.00"},
        eu_url: {"name": "Example Game", "title_id": "CUSA12345", "region": "EUR",
                 "version": "1.00", "size": 1234, "min_fw": "9.00"},
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "eu", "name": "Example_Game_CUSA12345_v1.00_[EU]_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert report["results"][0]["catalog_url"] == eu_url
    assert report["results"][0]["status"] == "matched"


def test_missing_source_region_is_reported_as_unverified(tmp_path):
    url = "https://archive.org/example/Example_Game_CUSA12345_v1.00_[US]_[EN]_[9.00].pkg"
    write_catalog(tmp_path, "ps4/games.json", {
        url: {"name": "Example Game", "title_id": "CUSA12345", "region": "USA",
              "version": "1.00", "size": 1234, "min_fw": "9.00"},
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})
    report = compare_scan(make_complete_scan([{
        "id": "no-region", "name": "Example_Game_CUSA12345_v1.00_[EN]_[9.00].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)
    assert report["results"][0]["status"] == "unverified"
    assert "region" in report["results"][0]["unavailable_metadata"]


def test_possible_duplicate_is_not_verified_or_cleaned_and_catalogs_unchanged(tmp_path):
    write_empty_catalogs(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    base = {
        "name": "Example_Game_CUSA12345_v1.00_[US]_[EN].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }
    scan = make_complete_scan([
        {"id": "one", **base},
        {"id": "two", **base},
    ])
    report = compare_scan(scan, tmp_path)
    after = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}

    assert len(report["possible_duplicates"]) == 1
    group = report["possible_duplicates"][0]
    assert group["file_ids"] == ["one", "two"]
    assert group["binary_verification"] == "unavailable_no_sha256_from_mocha"
    assert group["automatic_cleanup"] is False
    assert report["catalog_write_performed"] is False
    assert before == after


def test_comparison_refuses_incomplete_scan(tmp_path):
    with pytest.raises(ValueError, match="incomplete"):
        compare_scan({"scanner": "mocha", "scan_complete": False}, tmp_path)
