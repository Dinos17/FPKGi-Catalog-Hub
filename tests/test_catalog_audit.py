import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import catalog_audit  # noqa: E402


def test_audit_moves_generic_games_record_to_external_app_category(tmp_path, monkeypatch):
    ps4 = tmp_path / "ps4"
    ps5 = tmp_path / "ps5"
    ps4.mkdir()
    ps5.mkdir()

    misplaced = {
        "DATA": {
            "https://example.com/youtube.pkg": {
                "name": "YouTube",
                "title_id": "CUSA01116",
                "category": "games",
            }
        }
    }
    (ps4 / "games.json").write_text(json.dumps(misplaced), encoding="utf-8")
    (ps4 / "apps.json").write_text(json.dumps({"DATA": {}}), encoding="utf-8")

    monkeypatch.setattr(catalog_audit, "ROOT", tmp_path)
    monkeypatch.setattr(
        catalog_audit,
        "fetch_external_database_entries",
        lambda: {
            "https://example.com/youtube.pkg": {
                "name": "YouTube",
                "title_id": "CUSA01116",
                "category": "apps",
            }
        },
    )
    monkeypatch.setattr(catalog_audit, "resolve_categories", lambda title_ids: {})

    moved = catalog_audit.audit_catalogs()

    assert len(moved) == 1
    assert moved[0][1:] == ("CUSA01116", "games", "apps", "YouTube")
    assert json.loads((ps4 / "games.json").read_text(encoding="utf-8")) == {"DATA": {}}
    assert json.loads((ps4 / "apps.json").read_text(encoding="utf-8"))["DATA"][
        "https://example.com/youtube.pkg"
    ]["category"] == "apps"


def test_audit_keeps_specific_category_when_external_title_id_is_generic_game(
    tmp_path, monkeypatch
):
    ps4 = tmp_path / "ps4"
    ps5 = tmp_path / "ps5"
    ps4.mkdir()
    ps5.mkdir()

    dlc = {
        "DATA": {
            "https://example.com/dlc.pkg": {
                "name": "Example DLC",
                "title_id": "CUSA12345",
                "category": "dlc",
            }
        }
    }
    (ps4 / "dlc.json").write_text(json.dumps(dlc), encoding="utf-8")
    (ps4 / "games.json").write_text(json.dumps({"DATA": {}}), encoding="utf-8")

    monkeypatch.setattr(catalog_audit, "ROOT", tmp_path)
    monkeypatch.setattr(
        catalog_audit,
        "fetch_external_database_entries",
        lambda: {
            "https://example.com/game.pkg": {
                "name": "Example Game",
                "title_id": "CUSA12345",
                "category": "games",
            }
        },
    )
    monkeypatch.setattr(catalog_audit, "resolve_categories", lambda title_ids: {})

    moved = catalog_audit.audit_catalogs()

    assert moved == []
    assert json.loads((ps4 / "dlc.json").read_text(encoding="utf-8"))["DATA"][
        "https://example.com/dlc.pkg"
    ]["category"] == "dlc"
