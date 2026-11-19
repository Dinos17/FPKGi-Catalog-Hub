import time
from urllib.parse import urlsplit

import pytest

from tools.mocha_api_diagnostic import DiagnosticError, MochaDiagnostic, safe_http_diagnostics


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
        self.urls = []

    def get(self, url, params=None, timeout=None):
        route = urlsplit(url).path
        endpoints = {
            "/api/auth/me": "me",
            "/api/profile/summary": "summary",
            "/api/files": "files",
        }
        if route not in endpoints:
            raise AssertionError(f"Unexpected API route: {route}")
        key = (endpoints[route], (params or {}).get("path", ""),
               (params or {}).get("cursor", ""))
        self.calls.append(key)
        self.urls.append(url)
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

    assert {urlsplit(url).netloc for url in session.urls} == {"api.mocha.my"}
    assert {urlsplit(url).path for url in session.urls} == {
        "/api/auth/me", "/api/profile/summary", "/api/files"
    }
    assert report["authentication"] == "verified_by_file_listing"
    assert report["folder_count"] == 3
    assert report["file_count"] == 1
    assert report["listing_page_count"] == 4
    assert report["scan_complete"] is True
    assert "private.pkg" not in str(report)
    assert "private-id" not in str(report)


def test_diagnostic_traverses_repeated_relative_folder_name():
    responses = {
        ("me", "", ""): {"canUseApi": True, "canCreateHotlinks": False},
        ("summary", "", ""): {"canUseApi": True, "canCreateHotlinks": False},
        ("files", "", ""): {
            "files": [], "folders": ["PS4"], "hasMore": False, "nextCursor": None
        },
        ("files", "PS4", ""): {
            "files": [], "folders": ["PS4"], "hasMore": False, "nextCursor": None
        },
        ("files", "PS4/PS4", ""): {
            "files": [], "folders": [], "hasMore": False, "nextCursor": None
        },
    }
    session = FakeSession(responses)
    report = MochaDiagnostic("test-key", session=session).run()

    assert ("files", "PS4/PS4", "") in session.calls
    assert report["folder_count"] == 3
    assert report["scan_complete"] is True


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
        diagnostic.list_folder("", remaining_pages=100, deadline=time.monotonic() + 10)


def test_diagnostic_rejects_missing_next_cursor():
    responses = {
        ("files", "", ""): {
            "files": [], "folders": [], "hasMore": True, "nextCursor": None
        }
    }
    diagnostic = MochaDiagnostic("test-key", session=FakeSession(responses))

    with pytest.raises(DiagnosticError, match="no cursor"):
        diagnostic.list_folder("", remaining_pages=100, deadline=time.monotonic() + 10)



def test_get_json_http_error_exposes_only_validated_metadata():
    class ErrorResponse(FakeResponse):
        def __init__(self):
            super().__init__({
                "code": "private-token-123",
                "message": "private filename.pkg and /private/account/path",
                "token": "do-not-print-this",
            }, status_code=403)
            self.headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Server": "edge-gateway",
                "X-Request-Id": "private-request-token",
            }

    class ErrorSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            return ErrorResponse()

    diagnostic = MochaDiagnostic("test-key", session=ErrorSession())
    with pytest.raises(DiagnosticError) as caught:
        diagnostic.get_json("/files")

    rendered = str(caught.value)
    assert "HTTP 403" in rendered
    assert '"http_status": 403' in rendered
    assert '"request_id": null' in rendered
    assert '"error_code": null' in rendered
    assert "private-token-123" not in rendered
    assert "private-request-token" not in rendered
    assert "private filename.pkg" not in rendered
    assert "/private/account/path" not in rendered
    assert "do-not-print-this" not in rendered


def test_safe_http_diagnostics_accepts_only_known_safe_metadata():
    class SafeResponse:
        status_code = 404
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Server": "edge-gateway",
            "X-Request-Id": "123e4567-e89b-12d3-a456-426614174000",
        }

        def json(self):
            return {"code": "ROUTE_NOT_FOUND", "message": "not exposed"}

    report = safe_http_diagnostics(SafeResponse())
    assert report["http_status"] == 404
    assert report["content_type"] == "application/json"
    assert report["server"] == "edge-gateway"
    assert report["request_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert report["body_format"] == "json_object"
    assert report["error_code"] == "ROUTE_NOT_FOUND"


def test_global_page_budget_returns_partial_report(monkeypatch):
    import tools.mocha_api_diagnostic as diagnostic_module

    monkeypatch.setattr(diagnostic_module, "MAX_TOTAL_PAGES", 1)
    responses = {
        ("me", "", ""): {"canUseApi": True},
        ("summary", "", ""): {"canUseApi": True},
        ("files", "", ""): {
            "files": [], "folders": [], "hasMore": True, "nextCursor": "next"
        },
    }
    report = MochaDiagnostic("test-key", session=FakeSession(responses)).run()

    assert report["scan_complete"] is False
    assert report["listing_page_count"] == 1
    assert report["error"] == "Global page budget reached"



def test_folder_names_preserve_leading_and_trailing_whitespace():
    responses = {
        ("me", "", ""): {"canUseApi": True},
        ("summary", "", ""): {"canUseApi": True},
        ("files", "", ""): {
            "files": [], "folders": [" PS4 "], "hasMore": False, "nextCursor": None
        },
        ("files", " PS4 ", ""): {
            "files": [], "folders": [], "hasMore": False, "nextCursor": None
        },
    }
    session = FakeSession(responses)
    report = MochaDiagnostic("test-key", session=session).run()

    assert ("files", " PS4 ", "") in session.calls
    assert report["folder_count"] == 2
    assert report["scan_complete"] is True


def test_optional_summary_routes_can_be_unsupported():
    class SequenceSession:
        def __init__(self):
            self.headers = {}
            self.responses = [
                FakeResponse({"message": "unsupported"}, status_code=405),
                FakeResponse({"message": "unsupported"}, status_code=501),
                FakeResponse({
                    "files": [], "folders": [], "hasMore": False, "nextCursor": None
                }),
            ]

        def get(self, url, params=None, timeout=None):
            return self.responses.pop(0)

    report = MochaDiagnostic("test-key", session=SequenceSession()).run()
    assert report["authentication"] == "verified_by_file_listing"
    assert report["account_summary_available"] is False
    assert report["scan_complete"] is True


def test_transient_network_error_is_retried(monkeypatch):
    class FlakySession:
        def __init__(self):
            self.headers = {}
            self.calls = 0

        def get(self, url, params=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                import requests
                raise requests.Timeout("sensitive network detail")
            return FakeResponse({"ok": True})

    import tools.mocha_api_diagnostic as diagnostic_module
    monkeypatch.setattr(diagnostic_module.time, "sleep", lambda _: None)
    session = FlakySession()
    payload = MochaDiagnostic("test-key", session=session).get_json("/auth/me")

    assert payload == {"ok": True}
    assert session.calls == 2


def test_scan_reports_snapshot_consistency_limitation():
    responses = {
        ("me", "", ""): {"canUseApi": True},
        ("summary", "", ""): {"canUseApi": True},
        ("files", "", ""): {
            "files": [], "folders": [], "hasMore": False, "nextCursor": None
        },
    }
    report = MochaDiagnostic("test-key", session=FakeSession(responses)).run()
    assert report["scan_complete"] is True
    assert report["snapshot_consistency"] == "not_guaranteed_by_api"
