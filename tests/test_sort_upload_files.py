import json

from tools.sort_upload_files import DEFAULT_LIMIT_BYTES, main, scan_files


def test_splits_at_exact_limit_and_records_relative_metadata(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    (root / "exact.pkg").write_bytes(b"x" * 10)
    (root / "large.pkg").write_bytes(b"x" * 11)
    small, big, errors = scan_files(root, 10)
    assert errors == []
    assert [item["path"] for item in small] == ["exact.pkg"]
    assert [item["path"] for item in big] == ["large.pkg"]
    assert small[0]["size_bytes"] == 10
    assert small[0]["extension"] == ".pkg"


def test_scans_nested_directories_and_skips_symlinks(tmp_path):
    root = tmp_path / "input"
    nested = root / "PS4" / "homebrew"
    nested.mkdir(parents=True)
    (nested / "tool.pkg").write_bytes(b"abc")
    outside = tmp_path / "outside.pkg"
    outside.write_bytes(b"outside")
    try:
        (root / "escape.pkg").symlink_to(outside)
    except OSError:
        pass
    small, big, errors = scan_files(root, 100)
    assert errors == []
    assert [item["path"] for item in small] == ["PS4/homebrew/tool.pkg"]
    assert big == []


def test_default_limit_is_20_gib():
    assert DEFAULT_LIMIT_BYTES == 20_000_000_000


def test_cli_writes_two_json_reports(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "small.bin").write_bytes(b"a")
    (root / "large.bin").write_bytes(b"ab")
    output = tmp_path / "reports"
    result = main([str(root), "--limit-bytes", "1", "--output-dir", str(output)])
    assert result == 0
    small = json.loads((output / "small_file.json").read_text())
    big = json.loads((output / "big_file.json").read_text())
    assert small["count"] == 1
    assert big["count"] == 1
    assert small["files"][0]["name"] == "small.bin"
    assert big["files"][0]["name"] == "large.bin"
