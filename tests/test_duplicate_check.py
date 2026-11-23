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


@pytest.mark.parametrize(
    ("first", "second", "collides"),
    [
        ("Game%7EName.pkg", "Game~Name.pkg", True),
        ("Game%2FName.pkg", "Game/Name.pkg", False),
        ("Game%3Fedition=1.pkg", "Game?edition=1.pkg", False),
        ("Game%23part.pkg", "Game#part.pkg", False),
        ("Game%26Name.pkg", "Game&Name.pkg", False),
    ],
)
def test_url_normalization_preserves_reserved_path_delimiters(
    tmp_path, monkeypatch, capsys, first, second, collides
):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://archive.org/download/test/" + first: metadata(),
        "https://archive.org/download/test/" + second: metadata(version="02.00"),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    if collides:
        with pytest.raises(SystemExit):
            duplicate_check.check_duplicates()
    else:
        duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert ("**URL normalization collisions:** 1" in output) is collides


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


@pytest.mark.parametrize("bad_size", [0, "0", "²", "１２３", 12.0, -1])
def test_invalid_sizes_are_incomplete(tmp_path, monkeypatch, capsys, bad_size):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/a.pkg": metadata(size=bad_size),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "Records with incomplete identity metadata:** 1" in output
    assert "size" in output


@pytest.mark.parametrize("field", ["title_id", "region", "version"])
@pytest.mark.parametrize("bad_value", [[], 123, {}, None, ""])
def test_non_string_identity_fields_are_incomplete(tmp_path, monkeypatch, capsys, field, bad_value):
    write_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/a.pkg": metadata(**{field: bad_value}),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "Records with incomplete identity metadata:** 1" in output
    assert field in output


def test_distinct_dlc_names_are_not_duplicate(tmp_path, monkeypatch, capsys):
    write_catalog(tmp_path, "ps4/dlc.json", {
        "https://example.test/a.pkg": metadata(name="Expansion Pack A"),
        "https://example.test/b.pkg": metadata(name="Expansion Pack B"),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    assert "Duplicate package identities:** 0" in capsys.readouterr().out


def test_dlc_without_name_is_incomplete(tmp_path, monkeypatch, capsys):
    write_catalog(tmp_path, "ps4/dlc.json", {
        "https://example.test/a.pkg": metadata(),
    })
    monkeypatch.setattr(duplicate_check, "ROOT", tmp_path)
    duplicate_check.check_duplicates()
    output = capsys.readouterr().out
    assert "Records with incomplete identity metadata:** 1" in output
    assert "name" in output


def test_case_sensitive_mirror_filenames_are_not_exempt():
    identity = ("PS4", "games", "CUSA12345", "USA", "01.00", 1024)
    records = [
        ("ps4/games.json", "https://archive.org/download/test/Game.pkg"),
        ("ps4/games.json", "https://huggingface.co/datasets/test/files/game.pkg"),
    ]
    mirrors, duplicates = duplicate_check.classify_identity_duplicates({identity: records})
    assert mirrors == {}
    assert identity in duplicates


def test_markdown_report_escapes_untrusted_values():
    report = duplicate_check.build_report(
        {"ps4/games.json": {}},
        {"https://bad.test/`\\n# forged": ["ps4/`\\n# forged.json"]},
        {},
        {
            ("PS4", "games", "[click](https://evil.test)", "USA", "01.00", 1024): [
                ("ps4/games.json", "https://bad.test/package.pkg")
            ]
        },
        [],
    )
    assert "# forged" not in report
    assert "[click](https://evil.test)" not in report
    assert "\\\\[" in report
    assert duplicate_check.report_value("`") == "&#96;"
