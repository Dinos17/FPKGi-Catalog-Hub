import pytest

from tools.mocha_api_diagnostic import DiagnosticError, MochaDiagnostic


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self.payload

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        key = (url.rsplit("/", 1)[-1], (params or {}).get("path", ""),
               (params or {}).get("cursor", ""))
        self.calls.append(key)
        payload = self.responses[key]
        return FakeResponse(payload)


def test_diagnostic_walks_nested_folders_and_all_pages():
    responses = {
        ("me", "", ""): {"canUseApi": True, "canCreateHotlinks": False},
        ("summary", "", ""): {"canUseApi": True, "canCreateHotlinks": False},
        ("files", "", ""): {
            "files": [], "folders": ["PS4"], "hasMore": True, "nextCursor": "page-2"
        },
        ("files", "", "page-2"): {
            "files": [], "folders": [], "hasMore": False, "nextCursor": None
        },
        ("files", "PS4", ""): {
            "files": [], "folders": ["Games"], "hasMore": False, "nextCursor": None
        },
        ("files", "PS4/Games", ""): {
            "files": [{
                "id": "private-id", "original_name": "private.pkg",
                "size": 123, "path": "PS4/Games"
            }],
            "folders": [], "hasMore": False, "nextCursor": None
        },
    }
    session = FakeSession(responses)
    report = MochaDiagnostic("test-key", session=session).run()

    assert report["authentication"] == "success"
    assert report["folder_count"] == 3
    assert report["file_count"] == 1
    assert report["listing_page_count"] == 4
    assert report["scan_complete"] is True
    assert "private.pkg" not in str(report)
    assert "private-id" not in str(report)


def test_diagnostic_rejects_repeated_pagination_cursor():
    responses = {
        ("files", "", ""): {
            "files": [], "folders": [], "hasMore": True, "nextCursor": "same"
        },
        ("files", "", "same"): {
            "files": [], "folders": [], "hasMore": True, "nextCursor": "same"
        },
    }
    diagnostic = MochaDiagnostic("test-key", session=FakeSession(responses))

    with pytest.raises(DiagnosticError, match="cursor repeated"):
        diagnostic.list_folder("")


def test_diagnostic_rejects_missing_next_cursor():
    responses = {
        ("files", "", ""): {
            "files": [], "folders": [], "hasMore": True, "nextCursor": None
        }
    }
    diagnostic = MochaDiagnostic("test-key", session=FakeSession(responses))

    with pytest.raises(DiagnosticError, match="no cursor"):
        diagnostic.list_folder("")
