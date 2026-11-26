import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import validate_catalogs


def test_catalog_validation_rejects_duplicate_mocha_ids_across_files(tmp_path, monkeypatch):
    file_id = "d5ac3913-802b-4d68-9803-8f5c07c7640e"
    entry = {"name": "Same File", "download_source": "mocha"}
    (tmp_path / "ps4").mkdir()
    (tmp_path / "ps5").mkdir()
    (tmp_path / "ps4" / "games.json").write_text(
        json.dumps({"DATA": {file_id: entry}}), encoding="utf-8"
    )
    (tmp_path / "ps5" / "ps5-games.json").write_text(
        json.dumps({"DATA": {file_id: entry}}), encoding="utf-8"
    )
    monkeypatch.setattr(validate_catalogs, "ROOT", tmp_path)
    monkeypatch.setattr(validate_catalogs, "load_sources", lambda: {"games": []})

    with pytest.raises(ValueError, match="Duplicate Mocha file ID"):
        validate_catalogs.main()


def test_catalog_validation_accepts_unique_mocha_ids(tmp_path, monkeypatch):
    file_id = "d5ac3913-802b-4d68-9803-8f5c07c7640e"
    entry = {"name": "One File", "download_source": "mocha"}
    (tmp_path / "ps4").mkdir()
    (tmp_path / "ps5").mkdir()
    (tmp_path / "ps4" / "games.json").write_text(
        json.dumps({"DATA": {file_id: entry}}), encoding="utf-8"
    )
    (tmp_path / "ps5" / "ps5-games.json").write_text(
        json.dumps({"DATA": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(validate_catalogs, "ROOT", tmp_path)
    monkeypatch.setattr(validate_catalogs, "load_sources", lambda: {"games": []})

    validate_catalogs.main()



def test_catalog_validation_accepts_valid_deepstorage_registration(tmp_path, monkeypatch):
    url = (
        "https://share.deepstorage.dev/"
        "0123456789abcdef0123456789abcdef/Game.pkg"
    )
    (tmp_path / "ps4").mkdir()
    (tmp_path / "ps5").mkdir()
    (tmp_path / "ps4" / "games.json").write_text(
        json.dumps({"DATA": {url: {
            "name": "DeepStorage Game",
            "title_id": "CUSA12345",
            "size": 123,
            "version": "1.0",
            "download_source": "deepstorage",
        }}}),
        encoding="utf-8",
    )
    (tmp_path / "ps5" / "ps5-games.json").write_text(
        json.dumps({"DATA": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(validate_catalogs, "ROOT", tmp_path)
    monkeypatch.setattr(validate_catalogs, "load_sources", lambda: {"games": []})

    validate_catalogs.main()
