import json

from tools.sort_upload_files import (
    DEFAULT_LIMIT_BYTES,
    classify,
    discover_catalogs,
    load_entries,
    main,
)


def make_catalog(root, relative_path, data):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def test_discovers_and_reads_every_ps4_and_ps5_catalog(tmp_path):
    make_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/game.pkg": {
            "name": "Example Game", "title_id": "CUSA00001", "size": 20_000_000_000,
        },
    })
    make_catalog(tmp_path, "ps4/dlc.json", {
        "https://example.test/dlc.pkg": {"name": "Example DLC", "size": 20_000_000_001},
    })
    make_catalog(tmp_path, "ps4/homebrew.json", {
        "https://example.test/homebrew.pkg": {"name": "Example Homebrew", "size": 100},
    })
    make_catalog(tmp_path, "ps4/nested/extra.json", {
        "https://example.test/extra.pkg": {"name": "Nested Catalog Entry", "size": 200},
    })
    make_catalog(tmp_path, "ps5/ps5-games.json", {
        "https://example.test/ps5.pkg": {"name": "PS5 Game", "size": 300},
    })
    make_catalog(tmp_path, "ps5/ps5-apps.json", {
        "https://example.test/app.pkg": {"name": "PS5 App", "size": 400},
    })

    catalogs = discover_catalogs(tmp_path)
    entries, problems, scanned_paths = load_entries(tmp_path)

    assert len(catalogs) == 6
    assert len(scanned_paths) == 6
    assert problems == []
    assert len(entries) == 6
    assert {entry["category"] for entry in entries} == {
        "games", "dlc", "homebrew", "extra", "apps",
    }
    assert {entry["platform"] for entry in entries} == {"ps4", "ps5"}
    assert any(entry["category"] == "games" and entry["name"] == "Example Game" for entry in entries)
    assert any(entry["category"] == "dlc" and entry["name"] == "Example DLC" for entry in entries)


def test_classifies_exact_limit_and_unknown_sizes(tmp_path):
    make_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/exact.pkg": {"name": "Exact", "size": 20_000_000_000},
        "https://example.test/over.pkg": {"name": "Over", "size": 20_000_000_001},
        "https://example.test/missing.pkg": {"name": "Missing"},
        "https://example.test/invalid.pkg": {"name": "Invalid", "size": -1},
        "https://example.test/bool.pkg": {"name": "Boolean", "size": True},
    })
    entries, problems, _ = load_entries(tmp_path)
    assert problems == []
    small, big, unknown = classify(entries, DEFAULT_LIMIT_BYTES)
    assert [entry["name"] for entry in small] == ["Exact"]
    assert [entry["name"] for entry in big] == ["Over"]
    assert {entry["name"] for entry in unknown} == {"Missing", "Invalid", "Boolean"}


def test_default_limit_is_20_gb():
    assert DEFAULT_LIMIT_BYTES == 20_000_000_000


def test_reports_include_all_categories_and_scanned_catalogs(tmp_path):
    make_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/small.pkg": {"name": "Small Game", "size": 1},
        "https://example.test/big.pkg": {"name": "Big Game", "size": 2},
        "https://example.test/unknown.pkg": {"name": "Unknown Size"},
    })
    make_catalog(tmp_path, "ps4/dlc.json", {
        "https://example.test/dlc.pkg": {"name": "DLC", "size": 1},
    })
    make_catalog(tmp_path, "ps5/ps5-homebrew.json", {
        "https://example.test/homebrew.pkg": {"name": "Homebrew", "size": 1},
    })
    output = tmp_path / "reports"

    result = main([
        "--repo-root", str(tmp_path),
        "--limit-bytes", "1",
        "--output-dir", str(output),
    ])

    assert result == 0
    small = json.loads((output / "small_file.json").read_text(encoding="utf-8"))
    big = json.loads((output / "big_file.json").read_text(encoding="utf-8"))
    unknown = json.loads((output / "unknown_size.json").read_text(encoding="utf-8"))
    errors = json.loads((output / "sort_errors.json").read_text(encoding="utf-8"))
    assert small["count"] == 3
    assert big["count"] == 1
    assert unknown["count"] == 1
    assert errors["count"] == 0
    assert small["catalog_count"] == 3
    assert {entry["category"] for entry in small["entries"]} == {"dlc", "homebrew", "games"}
    assert small["entries"][0]["name"] == "DLC"
