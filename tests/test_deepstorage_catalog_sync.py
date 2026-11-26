import json
from pathlib import Path

import pytest

from tools import deepstorage_catalog_sync as sync


def _scan(files):
    return {
        "scanner": "deepstorage",
        "scan_complete": True,
        "read_only": True,
        "catalog_write_performed": False,
        "folder_path": "PS4/Games/",
        "files": files,
    }


def _record(url="https://share.deepstorage.dev/" + "a" * 32 + "/Example.pkg"):
    return {
        "url": url,
        "name": "Example [US] [1.00].pkg",
        "category": "games",
        "title_id": "CUSA12345",
        "title": "Example Game",
        "normalized_title": "example game",
        "region": "USA",
        "version": "01.00",
        "size": 123456,
        "min_fw": "09.000",
        "pkg_header": {"bounds_valid": True},
    }


def _catalog_root(tmp_path, games=None):
    (tmp_path / "ps4").mkdir()
    (tmp_path / "ps5").mkdir()
    (tmp_path / "ps4" / "games.json").write_text(
        json.dumps({"DATA": games or {}}), encoding="utf-8"
    )
    (tmp_path / "ps4" / "dlc.json").write_text(
        json.dumps({"DATA": {}}), encoding="utf-8"
    )
    return tmp_path


def test_refuses_incomplete_scan(tmp_path):
    with pytest.raises(ValueError, match="incomplete"):
        sync.sync_catalog({"scanner": "deepstorage", "scan_complete": False}, tmp_path)


def test_adds_new_verified_entry(monkeypatch, tmp_path):
    root = _catalog_root(tmp_path)
    monkeypatch.setattr(sync, "inspect_file", lambda item, folder: _record(item["download_url"]))
    url = "https://share.deepstorage.dev/" + "b" * 32 + "/New.pkg"
    report = sync.sync_catalog(_scan([{
        "name": "New.pkg", "extension": "pkg", "download_url": url,
        "size_bytes": 123456, "folder_path": "PS4/Games/",
    }]), root)

    data = json.loads((root / "ps4" / "games.json").read_text())
    assert url in data["DATA"]
    assert data["DATA"][url]["download_source"] == "deepstorage"
    assert report["counts"]["added"] == 1
    assert report["catalog_write_performed"] is True


def test_updates_unique_non_mocha_catalog_source(monkeypatch, tmp_path):
    old_url = "https://archive.org/download/example/Example%20Game%20%5BUS%5D%20%5B1.00%5D.pkg"
    old = {
        "title_id": "CUSA12345", "region": "USA", "name": "Example Game",
        "version": "01.00", "size": 123456, "min_fw": "09.000",
    }
    root = _catalog_root(tmp_path, {old_url: old})
    record = _record()
    monkeypatch.setattr(sync, "inspect_file", lambda item, folder: record)
    report = sync.sync_catalog(_scan([{
        "name": record["name"], "extension": "pkg", "download_url": record["url"],
        "size_bytes": record["size"], "folder_path": "PS4/Games/",
    }]), root)

    data = json.loads((root / "ps4" / "games.json").read_text())
    assert old_url not in data["DATA"]
    assert data["DATA"][record["url"]]["download_source"] == "deepstorage"
    assert report["counts"]["updated"] == 1


def test_never_replaces_mocha_registration(monkeypatch, tmp_path):
    old_url = "mocha-file-123"
    old = {
        "title_id": "CUSA12345", "region": "USA", "name": "Example Game",
        "version": "01.00", "size": 123456, "download_source": "mocha",
    }
    root = _catalog_root(tmp_path, {old_url: old})
    record = _record()
    monkeypatch.setattr(sync, "inspect_file", lambda item, folder: record)
    report = sync.sync_catalog(_scan([{
        "name": record["name"], "extension": "pkg", "download_url": record["url"],
        "size_bytes": record["size"], "folder_path": "PS4/Games/",
    }]), root)

    data = json.loads((root / "ps4" / "games.json").read_text())
    assert old_url in data["DATA"]
    assert record["url"] not in data["DATA"]
    assert report["counts"]["protected"] == 1


def test_duplicate_scanned_identity_is_not_registered(monkeypatch, tmp_path):
    root = _catalog_root(tmp_path)
    record_a = _record("https://share.deepstorage.dev/" + "c" * 32 + "/A.pkg")
    record_b = _record("https://share.deepstorage.dev/" + "d" * 32 + "/B.pkg")
    records = iter([record_a, record_b])
    monkeypatch.setattr(sync, "inspect_file", lambda item, folder: next(records))
    report = sync.sync_catalog(_scan([
        {"name": "A.pkg", "extension": "pkg", "download_url": record_a["url"], "size_bytes": 123456},
        {"name": "B.pkg", "extension": "pkg", "download_url": record_b["url"], "size_bytes": 123456},
    ]), root)

    data = json.loads((root / "ps4" / "games.json").read_text())
    assert data["DATA"] == {}
    assert report["counts"]["ambiguous"] == 2


def test_range_reader_rejects_redirect_to_non_deepstorage_host():
    class Response:
        status_code = 302
        headers = {"Location": "https://archive.org/package.pkg"}
        def close(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    reader = sync.DeepStorageRangeReader(
        "https://share.deepstorage.dev/" + "e" * 32 + "/Example.pkg",
        session=Session(),
    )
    with pytest.raises(ValueError, match="left the exact approved"):
        reader.read(0, 4)
