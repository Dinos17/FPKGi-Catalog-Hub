from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from tools.mocha_scanner import MochaScanner, ScanError
from tools.mocha_catalog_compare import compare_scan


class FakeResponse:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        return response


def payload(files=None, folders=None, *, has_more=False, cursor=None):
    return {
        "files": files or [],
        "folders": folders or [],
        "hasMore": has_more,
        "nextCursor": cursor,
    }


def file_row(file_id="f1", name="Example_Game_CUSA12345_v1.00_[US]_[EN].pkg",
             path="PS4/Games", size=1234, **extra):
    return {
        "id": file_id,
        "original_name": name,
        "size": size,
        "path": path,
        **extra,
    }


def test_scanner_recursively_reads_metadata_and_classifies_platform_category():
    session = FakeSession([
        FakeResponse({"canUseApi": True, "canCreateHotlinks": False}),
        FakeResponse({"canUseApi": True, "canCreateHotlinks": False}),
        FakeResponse(payload(folders=["PS4"])),
        FakeResponse(payload(folders=["Games"])),
        FakeResponse(payload(files=[file_row()])),
    ])

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


def test_scanner_uses_cursor_pagination_and_deduplicates_file_ids():
    session = FakeSession([
        FakeResponse({}),
        FakeResponse({}),
        FakeResponse(payload(files=[file_row()], has_more=True, cursor="next")),
        FakeResponse(payload(files=[file_row()], has_more=False)),
    ])

    report = MochaScanner("test-key", session, max_seconds=10).run()

    assert report["listing_page_count"] == 2
    assert report["file_count"] == 1
    assert session.calls[2][1]["params"]["path"] == ""
    assert session.calls[3][1]["params"]["cursor"] == "next"


def test_scanner_preserves_whitespace_in_folder_names():
    assert MochaScanner.child_path("PS4", " Games ") == "PS4/ Games "


def test_scanner_rejects_redirects_instead_of_forwarding_credentials():
    session = FakeSession([FakeResponse(status=302, headers={"Location": "https://evil.example/"})])
    scanner = MochaScanner("test-key", session, max_seconds=10)

    with pytest.raises(ScanError, match="redirected"):
        scanner.client.get_json("/auth/me", deadline=__import__("time").monotonic() + 10)


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


def make_complete_scan(files):
    return {
        "scanner": "mocha",
        "scan_complete": True,
        "catalog_write_performed": False,
        "snapshot_consistency": "not_guaranteed_by_api",
        "permissions": {"can_create_hotlinks": False},
        "files": files,
    }


def test_comparison_matches_catalog_metadata_without_writing(tmp_path):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/example.pkg": {
            "name": "Example Game", "title_id": "CUSA12345",
            "region": "USA", "version": "01.00", "size": 1234,
        },
    })
    for relative in ("ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})

    report = compare_scan(make_complete_scan([{
        "id": "f1", "name": "Example_Game_CUSA12345_v1.00_[US]_[EN].pkg",
        "extension": "pkg", "size_bytes": 1234, "platform": "PS4",
        "catalog_category": "games", "folder_path": "PS4/Games",
    }]), tmp_path)

    assert report["counts"]["matched"] == 1
    assert report["results"][0]["status"] == "matched"
    assert report["catalog_write_performed"] is False
    assert report["automatic_duplicate_cleanup_enabled"] is False


def test_possible_duplicate_is_not_treated_as_verified_or_cleaned(tmp_path):
    for relative in ("ps4/games.json", "ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
        write_catalog(tmp_path, relative, {})

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

    assert len(report["possible_duplicates"]) == 1
    group = report["possible_duplicates"][0]
    assert group["file_ids"] == ["one", "two"]
    assert group["binary_verification"] == "unavailable_no_sha256_from_mocha"
    assert group["automatic_cleanup"] is False
    assert report["catalog_write_performed"] is False


def test_comparison_refuses_incomplete_scan(tmp_path):
    with pytest.raises(ValueError, match="incomplete"):
        compare_scan({"scanner": "mocha", "scan_complete": False}, tmp_path)
