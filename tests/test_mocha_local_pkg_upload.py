from __future__ import annotations

from pathlib import Path

import pytest

from tools import mocha_local_pkg_upload as local
from tools import mocha_remote_pkg_upload as remote


def make_pkg(path: Path, size: int = remote.MIN_PART_BYTES + 17) -> Path:
    path.write_bytes(bytes.fromhex("7f434e54") + b"P" * (size - 4))
    return path


def test_classify_skips_exact_matches_and_separates_size_conflicts(tmp_path):
    exact = make_pkg(tmp_path / "same.pkg", 300)
    conflict = make_pkg(tmp_path / "different.pkg", 400)
    missing = make_pkg(tmp_path / "new.pkg", 500)

    remote_files = {
        "same.pkg": [{"id": "a", "name": "same.pkg", "size": 300}],
        "different.pkg": [{"id": "b", "name": "different.pkg", "size": 399}],
    }
    same, absent, conflicts = local.classify([exact, conflict, missing], remote_files)

    assert [p.name for p, _ in same] == ["same.pkg"]
    assert [p.name for p in absent] == ["new.pkg"]
    assert [p.name for p, _ in conflicts] == ["different.pkg"]


def test_classify_treats_duplicate_remote_names_as_conflicts(tmp_path):
    package = make_pkg(tmp_path / "ambiguous.pkg", 300)
    remote_files = {
        "ambiguous.pkg": [
            {"id": "a", "name": "ambiguous.pkg", "size": 300},
            {"id": "b", "name": "ambiguous.pkg", "size": 300},
        ]
    }
    same, absent, conflicts = local.classify([package], remote_files)
    assert same == []
    assert absent == []
    assert len(conflicts) == 1
    assert conflicts[0][1]["reason"] == "duplicate remote filename"


def test_validate_local_pkg_rejects_non_pkg_header(tmp_path):
    path = tmp_path / "bad.pkg"
    path.write_bytes(b"NOT_A_PKG" + b"x" * 300)
    with pytest.raises(remote.UploadError, match="Not a valid PS4 PKG header"):
        local.validate_local_pkg(path)


def test_validate_remote_path_rejects_traversal():
    with pytest.raises(remote.UploadError):
        local.validate_remote_path("/PS4/../Other")
    assert local.validate_remote_path("/PS4/Games/") == "/PS4/Games"


def test_local_upload_streams_parts_and_renames_only_after_completion(tmp_path, monkeypatch):
    path = make_pkg(tmp_path / "local.pkg")
    size, signature = local.validate_local_pkg(path)
    api_calls = []
    uploaded_parts = []

    def fake_api(_session, method, endpoint, _key, body=None, params=None, deadline=None):
        api_calls.append((method, endpoint, body, params))
        if endpoint == "/files/multipart/init":
            return {
                "uploadId": "upload-1",
                "key": "object-key",
                "nodeId": "node-1",
                "originalName": body["originalName"],
                "partSizeBytes": remote.MIN_PART_BYTES,
            }
        if endpoint == "/files/multipart/presigned":
            part = body["partNumbers"][0]
            return {"urls": [{"partNumber": part, "url": "https://stor11.mochaprox.li/part"}]}
        if endpoint == "/files/multipart/complete":
            return {"file": {"id": "mocha-id"}}
        if endpoint == "/files/mocha-id":
            assert method == "PATCH"
            assert body == {"originalName": "local.pkg"}
            return {}
        raise AssertionError(endpoint)

    def fake_put(_session, url, data, part_number, deadline=None):
        uploaded_parts.append((part_number, data))
        return f"etag-{part_number}"

    monkeypatch.setattr(remote, "_request_json", fake_api)
    monkeypatch.setattr(remote, "_put_part", fake_put)

    result = local.upload_local_pkg(
        path,
        api_key="",
        remote_path="/PS4/Games",
        expected_size=size,
        expected_signature=signature,
        session=object(),
    )

    assert result["file_id"] == "mocha-id"
    assert result["filename"] == "local.pkg"
    assert [number for number, _ in uploaded_parts] == [1, 2]
    assert b"".join(data for _, data in uploaded_parts) == path.read_bytes()
    assert [endpoint for _, endpoint, _, _ in api_calls][-1] == "/files/mocha-id"


def test_local_upload_aborts_if_source_changes_before_upload(tmp_path, monkeypatch):
    path = make_pkg(tmp_path / "changed.pkg")
    size, signature = local.validate_local_pkg(path)
    path.write_bytes(path.read_bytes() + b"changed")
    calls = []

    def fake_api(_session, method, endpoint, _key, body=None, params=None, deadline=None):
        calls.append(endpoint)
        if endpoint == "/files/multipart/abort":
            return {}
        raise AssertionError(f"Unexpected API call: {endpoint}")

    monkeypatch.setattr(remote, "_request_json", fake_api)
    with pytest.raises(remote.UploadError, match="changed before upload"):
        local.upload_local_pkg(
            path,
            api_key="",
            remote_path="/PS4/Games",
            expected_size=size,
            expected_signature=signature,
            session=object(),
        )
    assert calls == []
