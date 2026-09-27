import json
import sys
from pathlib import Path
import pytest
TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import duplicate_check  # noqa: E402

def test_detects_exact_duplicate_urls(tmp_path, monkeypatch):
    record = {"https://example.test/game.pkg": {"title_id": "CUSA12345", "version": "1.00"}}
    (tmp_path / "games.json").write_text(json.dumps({"DATA": record}), encoding="utf-8")
    (tmp_path / "apps.json").write_text(json.dumps({"DATA": record}), encoding="utf-8")
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        duplicate_check.check_duplicates()

def test_title_version_duplicate_is_warning_only(tmp_path, monkeypatch, capsys):
    (tmp_path / "games.json").write_text(json.dumps({"DATA": {"https://example.test/a.pkg": {"title_id": "CUSA12345", "version": "1.00"}}}), encoding="utf-8")
    (tmp_path / "apps.json").write_text(json.dumps({"DATA": {"https://example.test/b.pkg": {"title_id": "CUSA12345", "version": "1.00"}}}), encoding="utf-8")
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    assert "Possible title/version duplicates: 1" in capsys.readouterr().out

def test_ignores_unified_ps5_catalog(tmp_path, monkeypatch):
    record = {"https://example.test/game.pkg": {"title_id": "PPSA12345", "version": "1.00"}}
    (tmp_path / "ps5.json").write_text(json.dumps({"DATA": record}), encoding="utf-8")
    (tmp_path / "ps5-games.json").write_text(json.dumps({"DATA": record}), encoding="utf-8")
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()