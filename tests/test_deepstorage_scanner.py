from tools.deepstorage_scanner import DeepStorageScanner, ScanError, SHARE_USERNAME


class FakeResponse:
    status_code = 200
    def __init__(self, payload): self.payload = payload
    def json(self): return self.payload


class FakeSession:
    def __init__(self, listings):
        self.listings = listings
        self.headers = {}
        self.calls = []
    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        key = (params["path"], params["offset"])
        if key not in self.listings:
            raise AssertionError(f"unexpected listing request: {key}")
        return FakeResponse(self.listings[key])


def listing(files=(), folders=(), has_more=False, next_offset=None):
    return {"files": list(files), "folders": list(folders),
            "has_more": has_more, "next_offset": next_offset}


def pkg(name, marker):
    return {"file_name": name, "file_size": 123,
            "file_link": f"https://share.deepstorage.dev/{marker * 32}/{name}"}


def test_recursively_scans_only_fixed_ps4_and_ps5_trees():
    session = FakeSession({
        ("PS4/", 0): listing(folders=["Games", "DLC"]),
        ("PS4/Games/", 0): listing(files=[pkg("PS4.pkg", "a")], folders=["Updates"]),
        ("PS4/Games/Updates/", 0): listing(files=[pkg("Update.pkg", "b")]),
        ("PS4/DLC/", 0): listing(files=[pkg("DLC.pkg", "c")]),
        ("PS5/", 0): listing(folders=[{"name": "Games", "path": "/PS5/Games/"}]),
        ("PS5/Games/", 0): listing(files=[pkg("PS5.pkg", "d")]),
    })
    report = DeepStorageScanner(session=session).run()
    assert report["scan_complete"] is True
    assert report["username"] == SHARE_USERNAME == "fallen"
    assert report["root_folders"] == ["PS4/", "PS5/"]
    assert report["folder_count"] == 6
    assert report["file_count"] == 4
    assert {row["folder_path"] for row in report["files"]} == {
        "PS4/Games/", "PS4/Games/Updates/", "PS4/DLC/", "PS5/Games/"
    }
    assert all(call[0].endswith("/fallen") for call in session.calls)
    assert {call[1]["path"] for call in session.calls} == {
        "PS4/", "PS4/Games/", "PS4/Games/Updates/", "PS4/DLC/", "PS5/", "PS5/Games/"
    }


def test_paginates_each_folder_and_discovers_child_folders_once():
    session = FakeSession({
        ("PS4/", 0): listing(folders=["Games"], has_more=True, next_offset=2),
        ("PS4/", 2): listing(files=[pkg("root.pkg", "e")]),
        ("PS4/Games/", 0): listing(files=[pkg("game.pkg", "f")]),
        ("PS5/", 0): listing(),
    })
    report = DeepStorageScanner(session=session).run()
    assert report["listing_page_count"] == 4
    assert report["file_count"] == 2
    assert ("PS4/Games/", 0) in [(call[1]["path"], call[1]["offset"]) for call in session.calls]


def test_rejects_folder_traversal_and_nested_path_as_child():
    for bad in ("../outside", "Games/child", ".", "..", ""):
        session = FakeSession({("PS4/", 0): listing(folders=[bad])})
        with pytest.raises(ScanError):
            DeepStorageScanner(session=session).run()


def test_rejects_out_of_scope_folder_object_path():
    session = FakeSession({
        ("PS4/", 0): listing(folders=[{"path": "/PS4/../PS5/"}]),
    })
    with pytest.raises(ScanError):
        DeepStorageScanner(session=session).run()


def test_rejects_file_url_outside_deepstorage_host():
    session = FakeSession({
        ("PS4/", 0): listing(files=[{
            "file_name": "bad.pkg", "file_size": 1,
            "file_link": "https://example.com/file.pkg",
        }]),
    })
    with pytest.raises(ScanError, match="approved DeepStorage host"):
        DeepStorageScanner(session=session).run()


def test_folder_safety_limit_is_global():
    session = FakeSession({
        ("PS4/", 0): listing(folders=["Games"]),
        ("PS4/Games/", 0): listing(),
        ("PS5/", 0): listing(),
    })
    with pytest.raises(ScanError, match="Folder-count safety limit"):
        DeepStorageScanner(session=session, max_folders=2).run()


import pytest
