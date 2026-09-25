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


def test_asset_to_entry_supports_ps5_title_id_filename_fallback(monkeypatch):
    monkeypatch.setattr(
        release_sources,
        "extract_metadata",
        lambda _url, _size: (_ for _ in ()).throw(RuntimeError("bad pkg")),
    )

    result = release_sources.asset_to_entry(
        {
            "name": "PPSA12345_v1.0.0.pkg",
            "browser_download_url": "https://example.com/game.pkg",
            "size": 123,
        },
        {},
        "games",
    )

    assert result[1]["title_id"] == "PPSA12345"
    assert result[1]["version"] == "1.0.0"


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



def test_fetch_release_entries_keeps_ps4_out_of_ps5_catalog(monkeypatch):
    monkeypatch.setattr(
        release_sources,
        "fetch_releases",
        lambda: [
            {"tag_name": "PS4-apps", "assets_url": "https://example.com/ps4-assets"},
            {"tag_name": "PS5-apps", "assets_url": "https://example.com/ps5-assets"},
        ],
    )

    assets = {
        "https://example.com/ps4-assets": [
            {
                "name": "PS4_CUSA00127.pkg",
                "browser_download_url": "https://example.com/ps4.pkg",
                "size": 123,
            }
        ],
        "https://example.com/ps5-assets": [
            {
                "name": "PS5_PPSA00127.pkg",
                "browser_download_url": "https://example.com/ps5.pkg",
                "size": 456,
            }
        ],
    }
    monkeypatch.setattr(
        release_sources,
        "fetch_release_assets",
        lambda release: assets[release["assets_url"]],
    )
    monkeypatch.setattr(
        release_sources,
        "asset_to_entry",
        lambda asset, _release, _category, _cover_urls: (
            asset["browser_download_url"],
            {"name": asset["name"]},
        ),
    )

    entries, ps5_entries = release_sources.fetch_release_entries()

    assert set(entries["apps"]) == {
        "https://example.com/ps4.pkg",
        "https://example.com/ps5.pkg",
    }
    assert set(ps5_entries["apps"]) == {
        "https://example.com/ps5.pkg",
    }


def test_validate_ps5_catalogs_rejects_ps4_release_url():
    from validate_catalogs import validate_ps5_catalogs

    ps4_entry = {
        "https://github.com/example/releases/download/PS4-apps/CUSA12345.pkg": {
            "title_id": "CUSA12345",
            "name": "PS4 App",
            "size": 123,
            "version": "1.0",
        }
    }

    try:
        validate_ps5_catalogs(ps4_entry, {"apps": ps4_entry})
    except ValueError as exc:
        assert "PS4 release asset" in str(exc)
    else:
        raise AssertionError("Expected PS4 entry to be rejected")


def test_validate_ps5_catalogs_requires_unified_catalog_to_match_categories():
    from validate_catalogs import validate_ps5_catalogs

    category_entry = {
        "https://github.com/example/releases/download/PS5-games/PPSA12345.pkg": {
            "title_id": "PPSA12345",
            "name": "PS5 Game",
            "size": 123,
            "version": "1.0",
        }
    }

    try:
        validate_ps5_catalogs({}, {"games": category_entry})
    except ValueError as exc:
        assert "does not equal the union" in str(exc)
    else:
        raise AssertionError("Expected unified PS5 mismatch to be rejected")
