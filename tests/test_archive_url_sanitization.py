import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import replace_archive_links_with_hf as target  # noqa: E402


def test_archive_pkg_accepts_archive_org_and_subdomains():
    assert target.archive_pkg("https://archive.org/download/game.pkg")
    assert target.archive_pkg("https://www.archive.org/download/game.pkg")
    assert target.archive_pkg("https://files.example.archive.org/game.PKG")


def test_archive_pkg_rejects_hostname_suffix_spoofing():
    assert not target.archive_pkg("https://fakearchive.org/download/game.pkg")
    assert not target.archive_pkg("https://not-archive.org/download/game.pkg")
    assert not target.archive_pkg("https://archive.org.evil.example/download/game.pkg")


def test_archive_pkg_requires_pkg_path():
    assert not target.archive_pkg("https://archive.org/download/game.zip")
    assert not target.archive_pkg("https://example.com/download/game.pkg")
