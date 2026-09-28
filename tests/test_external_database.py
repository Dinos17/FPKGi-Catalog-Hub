import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import external_database  # noqa: E402


def test_load_database_url_reads_json_config(tmp_path, monkeypatch):
    config_path = tmp_path / "external_database.json"
    config_path.write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "name": "Example",
                        "url": "https://huggingface.co/datasets/example/packages",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(external_database, "CONFIG_PATH", config_path)

    assert (
        external_database.load_database_url()
        == "https://huggingface.co/datasets/example/packages"
    )


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        {},
        {"url": ""},
        {"url": None},
        {"url": "http://huggingface.co/datasets/example/packages"},
        {"url": "https://user:pass@huggingface.co/datasets/example/packages"},
        {"url": "https://huggingface.co:443/datasets/example/packages"},
    ],
)
def test_load_database_url_rejects_invalid_config(tmp_path, monkeypatch, config):
    config_path = tmp_path / "external_database.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(external_database, "CONFIG_PATH", config_path)

    with pytest.raises(ValueError):
        external_database.load_database_url()


def test_load_database_urls_reads_multiple_datasets(tmp_path, monkeypatch):
    config_path = tmp_path / "external_database.json"
    config_path.write_text(
        json.dumps(
            {
                "datasets": [
                    {"name": "PS-Games-Dataset", "url": "https://huggingface.co/datasets/example/games"},
                    {"name": "PS-Applications", "url": "https://huggingface.co/datasets/example/apps"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(external_database, "CONFIG_PATH", config_path)

    assert external_database.load_database_urls() == [
        {"name": "PS-Games-Dataset", "url": "https://huggingface.co/datasets/example/games"},
        {"name": "PS-Applications", "url": "https://huggingface.co/datasets/example/apps"},
    ]


def test_load_database_urls_rejects_duplicate_urls(tmp_path, monkeypatch):
    config_path = tmp_path / "external_database.json"
    config_path.write_text(
        json.dumps(
            {
                "datasets": [
                    {"name": "one", "url": "https://huggingface.co/datasets/example/games"},
                    {"name": "two", "url": "https://huggingface.co/datasets/example/games"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(external_database, "CONFIG_PATH", config_path)

    with pytest.raises(ValueError, match="Duplicate external database URL"):
        external_database.load_database_urls()


def test_normalize_category_maps_gdc_to_dlc():
    assert external_database.normalize_category("gdc") == "dlc"


def test_hugging_face_dataset_parts_accepts_configured_database():
    assert external_database._hugging_face_dataset_parts(
        "https://huggingface.co/datasets/dinos17/FPKGi-Packages"
    ) == ("dinos17", "FPKGi-Packages")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/datasets/dinos17/FPKGi-Packages",
        "https://huggingface.co/dinos17/FPKGi-Packages",
        "https://huggingface.co/datasets/dinos17",
        "https://huggingface.co/datasets/dinos17/FPKGi-Packages/tree/main",
    ],
)
def test_hugging_face_dataset_parts_rejects_unsupported_urls(url):
    with pytest.raises(ValueError):
        external_database._hugging_face_dataset_parts(url)


def test_package_url_uses_configured_database():
    url = external_database.package_url(
        "https://huggingface.co/datasets/dinos17/FPKGi-Packages",
        "folder/My Game.pkg",
    )

    assert url == (
        "https://huggingface.co/datasets/dinos17/FPKGi-Packages/"
        "resolve/main/folder%2FMy%20Game.pkg?download=true"
    )


def test_parse_title_id():
    assert external_database.parse_title_id("GAME [CUSA12345].pkg") == "CUSA12345"
    assert external_database.parse_title_id("GAME [PPSA54321].pkg") == "PPSA54321"
    assert external_database.parse_title_id("GAME.pkg") is None


def test_scan_database_resolved_app_overrides_generic_games(monkeypatch):
    monkeypatch.setattr(
        external_database,
        "fetch_database_files",
        lambda url: [{"path": "PS4_CUSA01116.pkg", "size": 123}],
    )
    monkeypatch.setattr(
        external_database,
        "extract_metadata",
        lambda url, size: {
            "title_id": "CUSA01116",
            "region": "EU",
            "name": "YouTube",
            "version": "1.00",
            "release": None,
            "size": size,
            "min_fw": "5.00",
            "cover_url": None,
            "category": "games",
        },
    )
    monkeypatch.setattr(external_database, "resolve_category", lambda title_id: "apps")

    entries, scanned, skipped = external_database._scan_database(
        {
            "name": "PS-Games-Dataset",
            "url": "https://huggingface.co/datasets/example/games",
        }
    )

    assert scanned == 1
    assert skipped == 0
    assert len(entries) == 1
    assert next(iter(entries.values()))["category"] == "apps"


def test_fetch_external_database_entries_prefers_application_database_on_conflict(
    monkeypatch,
):
    url = "https://example.com/shared.pkg"

    def fake_scan(database):
        if database["name"] == "PS-Games-Dataset":
            return (
                {
                    url: {
                        "title_id": "CUSA01116",
                        "name": "YouTube",
                        "category": "games",
                    }
                },
                1,
                0,
            )
        return (
            {
                url: {
                    "title_id": "CUSA01116",
                    "name": "YouTube",
                    "category": "apps",
                }
            },
            1,
            0,
        )

    monkeypatch.setattr(
        external_database,
        "load_database_urls",
        lambda: [
            {"name": "PS-Games-Dataset", "url": "https://example.com/games"},
            {"name": "PS-Applications", "url": "https://example.com/apps"},
        ],
    )
    monkeypatch.setattr(external_database, "_scan_database", fake_scan)

    entries = external_database.fetch_external_database_entries()

    assert entries[url]["category"] == "apps"
