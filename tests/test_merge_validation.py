import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from merge import validate_entry, validate_entries  # noqa: E402


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


def test_validate_entry_warns_for_invalid_title_id_without_rejecting():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {"name": "Example Game", "title_id": "NOT-A-TITLE-ID"},
    )

    assert errors == []
    assert any("invalid title_id" in warning for warning in warnings)


def test_validate_entry_warns_for_invalid_size_without_rejecting():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {"name": "Example Game", "size": 0},
    )

    assert errors == []
    assert any("invalid size" in warning for warning in warnings)


def test_validate_entry_warns_for_invalid_version_without_rejecting():
    errors, warnings = validate_entry(
        "https://example.com/game.pkg",
        {"name": "Example Game", "version": "v1"},
    )

    assert errors == []
    assert any("invalid version" in warning for warning in warnings)


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
        "https://example.com/warned.pkg",
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
