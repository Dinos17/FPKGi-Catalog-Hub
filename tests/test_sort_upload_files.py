import json

from tools.sort_upload_files import (
    DEFAULT_LIMIT_BYTES,
    classify,
    load_entries,
    main,
)


def make_catalog(root, relative_path, data):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATA": data}), encoding="utf-8")


def test_reads_only_ps4_and_ps5_homebrew_metadata(tmp_path):
    make_catalog(tmp_path, "ps4/homebrew.json", {
        "https://example.test/small.pkg": {
            "name": "Small Homebrew", "title_id": "HB000001",
            "version": "1.0", "size": 20_000_000_000,
        },
        "https://example.test/large.pkg": {
            "name": "Large Homebrew", "title_id": "HB000002",
            "version": "1.0", "size": 20_000_000_001,
        },
        "https://example.test/unknown.pkg": {"name": "Unknown size"},
    })
    make_catalog(tmp_path, "ps5/ps5-homebrew.json", {
        "https://example.test/ps5.pkg": {"name": "PS5 Tool", "size": 123}
    })
    # Commercial catalogs are deliberately not read by this sorter.
    make_catalog(tmp_path, "ps4/games.json", {
        "https://example.test/commercial.pkg": {"name": "Excluded", "size": 1}
    })

    entries, problems = load_entries(tmp_path)
    assert problems == []
    assert len(entries) == 4
    assert all(entry["category"] == "homebrew" for entry in entries)
    assert not any("Excluded" == entry["name"] for entry in entries)


def test_classifies_exact_limit_and_unknown_sizes(tmp_path):
    make_catalog(tmp_path, "ps4/homebrew.json", {
        "https://example.test/exact.pkg": {"name": "Exact", "size": 10},
        "https://example.test/over.pkg": {"name": "Over", "size": 11},
        "https://example.test/missing.pkg": {"name": "Missing"},
        "https://example.test/invalid.pkg": {"name": "Invalid", "size": -1},
    })
    make_catalog(tmp_path, "ps5/ps5-homebrew.json", {})
    entries, problems = load_entries(tmp_path)
    assert problems == []
    small, big, unknown = classify(entries, 10)
    assert [entry["name"] for entry in small] == ["Exact"]
    assert [entry["name"] for entry in big] == ["Over"]
    assert {entry["name"] for entry in unknown} == {"Missing", "Invalid"}


def test_default_limit_is_20_gb():
    assert DEFAULT_LIMIT_BYTES == 20_000_000_000


def test_cli_writes_catalog_reports(tmp_path):
    make_catalog(tmp_path, "ps4/homebrew.json", {
        "https://example.test/small.pkg": {"name": "Small", "size": 1},
        "https://example.test/big.pkg": {"name": "Big", "size": 2},
        "https://example.test/unknown.pkg": {"name": "Unknown"},
    })
    make_catalog(tmp_path, "ps5/ps5-homebrew.json", {})
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
    assert small["count"] == 1
    assert big["count"] == 1
    assert unknown["count"] == 1
    assert small["entries"][0]["name"] == "Small"
    assert big["entries"][0]["name"] == "Big"
