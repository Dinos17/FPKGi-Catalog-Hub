import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from merge import merge_category, validate_entry, validate_entries  # noqa: E402


def test_validate_entry_accepts_valid_entry():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {
            "name": "Example Game",
            "title_id": "CUSA12345",
            "size": 123456,
            "version": "1.2.3",
        },
    )

    assert errors == []
    assert warnings == []


@pytest.mark.parametrize(
    "url",
    [
        "",
        None,
        "ftp://example.com/game.pkg",
        "example.com/game.pkg",
    ],
)
def test_validate_entry_rejects_invalid_package_url(url):
    errors, warnings = validate_entry(url, {"name": "Example Game"})

    assert errors
    assert any("package URL" in error for error in errors)
    assert warnings == []


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        "not-an-object",
        {"name": object()},
    ],
)
def test_validate_entry_rejects_non_json_metadata_types(metadata):
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        metadata,
    )

    assert errors
    assert warnings == []


def test_validate_entry_rejects_invalid_title_id():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {"name": "Example Game", "title_id": "NOT-A-TITLE-ID"},
    )

    assert any("invalid title_id" in error for error in errors)
    assert warnings == []


def test_validate_entry_rejects_invalid_size():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {"name": "Example Game", "size": 0},
    )

    assert any("invalid size" in error for error in errors)
    assert warnings == []


def test_validate_entry_rejects_invalid_version():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {"name": "Example Game", "version": "v1"},
    )

    assert any("invalid version" in error for error in errors)
    assert warnings == []


def test_validate_entry_warns_when_name_is_missing():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {"title_id": "CUSA12345"},
    )

    assert errors == []
    assert any("missing name" in warning for warning in warnings)


def test_validate_entries_keeps_valid_entries_and_rejects_errors():
    entries = {
        "https://example.com/valid.pkg": {
            "name": "Valid Game",
            "title_id": "CUSA12345",
        },
        "ftp://example.com/rejected.pkg": {
            "name": "Rejected Game",
        },
        "https://example.com/warned.pkg": {
            "name": "Warned Game",
            "version": "invalid",
        },
    }

    result = validate_entries(entries, "test source")

    assert set(result) == {
        "https://example.com/valid.pkg",
    }


def test_validate_entries_preserves_metadata_values():
    metadata = {
        "name": "Example Game",
        "title_id": "CUSA12345",
        "size": 123,
        "version": "1.0",
        "cover_url": None,
    }

    result = validate_entries(
        {"https://example.com/game.pkg": metadata},
        "test source",
    )

    assert result["https://example.com/game.pkg"] == metadata


def test_merge_category_preserves_existing_catalog_when_result_is_empty(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "games.json"
    existing = {
        "DATA": {
            "https://example.com/existing.pkg": {
                "name": "Existing Game",
            }
        }
    }
    output_path.write_text(
        __import__("json").dumps(existing),
        encoding="utf-8",
    )

    monkeypatch.setattr("merge.OUTPUT_DIR", tmp_path)

    def failing_source(_url):
        raise RuntimeError("temporary upstream failure")

    monkeypatch.setattr("merge.fetch_source", failing_source)

    result = merge_category("games", ["https://example.com/source.json"], {})

    assert result == 1
    assert __import__("json").loads(
        output_path.read_text(encoding="utf-8")
    ) == existing

def test_main_preserves_existing_ps5_catalog_when_release_result_is_empty(
    tmp_path, monkeypatch
):
    import json
    import merge

    output_path = tmp_path / "ps5-games.json"
    existing = {
        "DATA": {
            "https://github.com/example/PS5-game.pkg": {
                "name": "Existing PS5 Game",
                "title_id": "PPSA12345",
                "size": 123,
                "version": "1.0",
            }
        }
    }
    output_path.write_text(json.dumps(existing), encoding="utf-8")

    monkeypatch.setattr(merge, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        merge,
        "load_sources",
        lambda: {"games": []},
    )
    monkeypatch.setattr(
        merge,
        "fetch_release_entries",
        lambda: ({}, {"games": {}}),
    )
    monkeypatch.setattr(
        merge,
        "merge_category",
        lambda category, urls, release_entries: 0,
    )

    merge.main()

    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_merge_category_preserves_catalog_on_sharp_shrink_after_source_failure(
    tmp_path, monkeypatch
):
    import json
    import merge

    output_path = tmp_path / "games.json"
    existing = {
        "DATA": {
            f"https://example.com/existing-{index}.pkg": {
                "name": f"Existing Game {index}",
            }
            for index in range(10)
        }
    }
    output_path.write_text(json.dumps(existing), encoding="utf-8")

    monkeypatch.setattr(merge, "OUTPUT_DIR", tmp_path)

    def source(url):
        if "failed" in url:
            raise RuntimeError("temporary upstream failure")
        return {
            "https://example.com/new.pkg": {
                "name": "New Game",
            }
        }

    monkeypatch.setattr(merge, "fetch_source", source)

    result = merge.merge_category(
        "games",
        [
            "https://example.com/healthy-source.json",
            "https://example.com/failed-source.json",
        ],
        {},
    )

    assert result == 10
    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_merge_category_allows_shrink_when_sources_succeed(
    tmp_path, monkeypatch
):
    import json
    import merge

    output_path = tmp_path / "games.json"
    existing = {
        "DATA": {
            f"https://example.com/existing-{index}.pkg": {
                "name": f"Existing Game {index}",
            }
            for index in range(10)
        }
    }
    output_path.write_text(json.dumps(existing), encoding="utf-8")

    monkeypatch.setattr(merge, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        merge,
        "fetch_source",
        lambda _url: {
            "https://example.com/new.pkg": {
                "name": "New Game",
            }
        },
    )

    result = merge.merge_category(
        "games",
        ["https://example.com/healthy-source.json"],
        {},
    )

    assert result == 1
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "DATA": {
            "https://example.com/new.pkg": {
                "name": "New Game",
            }
        }
    }
