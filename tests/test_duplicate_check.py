import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import duplicate_check  # noqa: E402


@pytest.fixture(autouse=True)
def catalog_dirs(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    (tmp_path / "ps4").mkdir()
    (tmp_path / "ps5").mkdir()


def metadata(**overrides):
    result = {
        "title_id": "CUSA12345",
        "region": "USA",
        "version": "01.00",
        "size": 1024,
    }
    result.update(overrides)
    return result


def write_catalog(root, path, entries):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"DATA": entries}), encoding="utf-8")


def test_detects_exact_duplicate_urls_across_catalogs(tmp_path, monkeypatch):
    url = "https://example.test/game.pkg"
    write_catalog(tmp_path, "ps4/games.json", {url: metadata()})
    write_catalog(tmp_path, "ps4/dlc.json", {url: metadata()})
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        duplicate_check.check_duplicates()


def test_detects_same_complete_identity_at_different_urls(tmp_path, monkeypatch, capsys):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/a.pkg": metadata(),
        "https://mirror.test/b.pkg": metadata(),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        duplicate_check.check_duplicates()
    assert "**Duplicate package identities:** 1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "second",
    [
        metadata(region="EUR"),
        metadata(size=2048),
        metadata(version="02.00"),
    ],
)
def test_different_region_version_or_size_is_not_duplicate(
    tmp_path, monkeypatch, capsys, second
):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/a.pkg": metadata(),
        "https://example.test/b.pkg": second,
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "**Duplicate package identities:** 0" in output


def test_game_and_dlc_with_same_title_version_are_distinct(tmp_path, monkeypatch, capsys):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/game.pkg": metadata(),
    })
    write_catalog(tmp_path, "ps4/dlc.json", {
        "https://example.test/dlc.pkg": metadata(),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    assert "**Duplicate package identities:** 0" in capsys.readouterr().out


def test_percent_encoded_path_variants_are_detected_as_same_url(
    tmp_path, monkeypatch, capsys
):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/test/Game & Test.pkg": metadata(),
        "https://archive.org/download/test/Game %26 Test.pkg": metadata(),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "**Exact duplicate package URLs (normalized):** 1" in output


def test_archive_and_huggingface_pair_is_reported_as_unverified(
    tmp_path, monkeypatch, capsys
):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/test/Game.pkg": metadata(),
        "https://huggingface.co/datasets/test/files/Game.pkg": metadata(),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "**Possible Archive.org ↔ Hugging Face mirrors (unverified):** 1" in output
    assert "not binary-verified" in output
    assert "**Duplicate package identities:** 0" in output


def test_mirror_pair_is_not_exempt_when_identity_has_another_url():
    identity = ("PS4", "games", "CUSA12345", "USA", "01.00", 1024)
    records = [
        ("ps4/games.json", "https://archive.org/download/test/Game.pkg"),
        ("ps4/games.json", "https://huggingface.co/datasets/test/files/Game.pkg"),
        ("ps4/games.json", "https://third.example/Game.pkg"),
    ]
    mirrors, duplicates = duplicate_check.classify_identity_duplicates(
        {identity: records}
    )
    assert mirrors == {}
    assert identity in duplicates


def test_incomplete_identity_is_reported_not_guessed(tmp_path, monkeypatch, capsys):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/incomplete.pkg": {
            "title_id": "CUSA12345",
            "version": "01.00",
        },
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "**Records with incomplete identity metadata:** 1" in output
    assert "region, size" in output


def test_detects_duplicate_across_ps5_catalogs(tmp_path, monkeypatch):
    url = "https://example.test/game.pkg"
    write_catalog(tmp_path, "ps5/ps5-games.json", {url: metadata(title_id="PPSA12345")})
    write_catalog(tmp_path, "ps5/ps5-apps.json", {url: metadata(title_id="PPSA12345")})
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        duplicate_check.check_duplicates()


def test_invalid_size_is_incomplete_not_truncated(tmp_path, monkeypatch, capsys):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/game.pkg": metadata(size=12.5),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "**Records with incomplete identity metadata:** 1" in output
    assert "size" in output
