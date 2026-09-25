import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import release_sources  # noqa: E402


def test_parse_cover_urls_reads_package_table():
    release = {
        "body": """| Package | Cover URL |
| --- | --- |
| `GAME.pkg` | https://example.com/game.png |
| OTHER.pkg | https://example.com/other.png |
"""
    }

    assert release_sources.parse_cover_urls(release) == {
        "GAME.pkg": "https://example.com/game.png",
        "OTHER.pkg": "https://example.com/other.png",
    }


def test_parse_cover_urls_ignores_invalid_rows():
    release = {
        "body": """| Package | Cover URL |
| --- | --- |
| GAME.txt | https://example.com/game.png |
| GAME.pkg | not-a-url |
"""
    }

    assert release_sources.parse_cover_urls(release) == {}


def test_asset_to_entry_uses_filename_fallback_when_metadata_fails(monkeypatch):
    monkeypatch.setattr(
        release_sources,
        "extract_metadata",
        lambda _url, _size: (_ for _ in ()).throw(RuntimeError("bad pkg")),
    )

    asset = {
        "name": "CUSA12345_v1.2.3.pkg",
        "browser_download_url": "https://example.com/game.pkg",
        "size": 123,
    }
    release = {"published_at": "2026-09-24T12:00:00Z"}

    result = release_sources.asset_to_entry(
        asset,
        release,
        "games",
        {"CUSA12345_v1.2.3.pkg": "https://example.com/cover.png"},
    )

    url, metadata = result
    assert url == "https://example.com/game.pkg"
    assert metadata["title_id"] == "CUSA12345"
    assert metadata["version"] == "1.2.3"
    assert metadata["size"] == 123
    assert metadata["release"] == "24-09-2026"
    assert metadata["cover_url"] == "https://example.com/cover.png"


def test_asset_to_entry_rejects_non_pkg_assets():
    asset = {
        "name": "README.txt",
        "browser_download_url": "https://example.com/readme.txt",
        "size": 10,
    }

    assert release_sources.asset_to_entry(asset, {}, "games") is None


def test_fetch_paginated_json_follows_next_link(monkeypatch):
    class Response:
        def __init__(self, data, links):
            self._data = data
            self.links = links

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs["params"]["page"])
        if kwargs["params"]["page"] == 1:
            return Response([{"id": 1}], {"next": {"url": "next"}})
        return Response([{"id": 2}], {})

    monkeypatch.setattr(release_sources.requests, "get", fake_get)

    assert release_sources.fetch_paginated_json("https://api.example.com") == [
        {"id": 1},
        {"id": 2},
    ]
    assert calls == [1, 2]
