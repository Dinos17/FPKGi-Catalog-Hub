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
            {"datasets": [{"name": "one", "url": "https://huggingface.co/datasets/example/packages"}]}
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
        {"datasets": []},
        {"datasets": [{}]},
        {"datasets": [{"url": ""}]},
        {"datasets": [{"url": None}]},
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
