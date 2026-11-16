import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import akirabox_scanner


def flight_html(folder, items, total, page=1, per_page=20):
    payload = [
        folder,
        {
            "items": items,
            "folderId": 123,
            "search": "",
            "clearHref": "/folder",
        },
        {
            "id": folder["id"],
            "search": "",
            "sort": "created_at",
            "order": "desc",
            "total": total,
            "page": page,
            "perPage": per_page,
        },
    ]
    flight = " ".join(json.dumps(item) for item in payload)
    return "<script>self.__next_f.push([1," + json.dumps(flight) + "]);</script>"


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.headers = {}

    def get(self, url, timeout):
        if url not in self.pages:
            raise AssertionError(f"Unexpected URL requested: {url}")
        return FakeResponse(self.pages[url])


def test_scans_nested_public_folders_and_extracts_file_metadata():
    root = "https://akirabox.to/ROOT123/folder"
    child = "https://akirabox.to/CHILD123/folder"
    pages = {
        root: flight_html(
            {"id": "ROOT123", "name": "Homebrew", "isPrivate": False},
            [{"id": "CHILD123", "name": "Emulators", "kind": "folder"}],
            total=1,
        ),
        child: flight_html(
            {"id": "CHILD123", "name": "Emulators", "isPrivate": False},
            [{
                "id": "FILE123",
                "numericId": 42,
                "name": "Example_Emulator.pkg",
                "extension": "PKG",
                "size": 123456,
                "kind": "file",
                "visibility": "public",
                "createdAt": "2026-11-16T15:00:00.000Z",
                "parentId": 123,
            }],
            total=1,
        ),
    }
    scanner = akirabox_scanner.AkiraBoxScanner(session=FakeSession(pages))
    result = scanner.scan([{"label": "PS4 homebrew", "category": "homebrew", "url": root}])

    assert result.errors == []
    assert [folder["name"] for folder in result.folders] == ["Homebrew", "Emulators"]
    assert len(result.files) == 1
    assert result.files[0]["id"] == "FILE123"
    assert result.files[0]["extension"] == "pkg"
    assert result.files[0]["size_bytes"] == 123456
    assert result.files[0]["folder_path"] == ["Homebrew", "Emulators"]


def test_scans_all_pages_using_pager_metadata():
    root = "https://akirabox.to/ROOT123/folder"
    pages = {
        root: flight_html(
            {"id": "ROOT123", "name": "Homebrew", "isPrivate": False},
            [{"id": "FILE001", "name": "first.pkg", "extension": "pkg",
              "size": 10, "kind": "file", "visibility": "public"}],
            total=2,
            page=1,
            per_page=1,
        ),
        root + "?page=2": flight_html(
            {"id": "ROOT123", "name": "Homebrew", "isPrivate": False},
            [{"id": "FILE002", "name": "second.pkg", "extension": "pkg",
              "size": 20, "kind": "file", "visibility": "public"}],
            total=2,
            page=2,
            per_page=1,
        ),
    }
    scanner = akirabox_scanner.AkiraBoxScanner(session=FakeSession(pages))
    result = scanner.scan([{"label": "Homebrew", "category": "homebrew", "url": root}])

    assert result.errors == []
    assert [item["id"] for item in result.files] == ["FILE001", "FILE002"]


def test_rejects_non_allowlisted_categories_without_requesting_them():
    scanner = akirabox_scanner.AkiraBoxScanner(session=FakeSession({}))
    result = scanner.scan([{
        "label": "Games",
        "category": "games",
        "url": "https://akirabox.to/ROOT123/folder",
    }])

    assert result.files == []
    assert len(result.errors) == 1
    assert "not enabled" in result.errors[0]["error"]


def test_rejects_private_folder():
    root = "https://akirabox.to/ROOT123/folder"
    pages = {
        root: flight_html(
            {"id": "ROOT123", "name": "Private", "isPrivate": True},
            [],
            total=0,
        )
    }
    scanner = akirabox_scanner.AkiraBoxScanner(session=FakeSession(pages))
    result = scanner.scan([{"label": "Private", "category": "homebrew", "url": root}])

    assert result.files == []
    assert len(result.errors) == 1
    assert "private" in result.errors[0]["error"]


def test_rejects_non_akirabox_urls():
    try:
        akirabox_scanner._validate_folder_url("https://example.com/ROOT123/folder")
    except akirabox_scanner.ScanError:
        pass
    else:
        raise AssertionError("Expected unsupported host to be rejected")
