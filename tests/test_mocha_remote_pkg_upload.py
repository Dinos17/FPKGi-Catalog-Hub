from __future__ import annotations

import pytest

from tools import mocha_remote_pkg_upload as upload


class FakeResponse:
    def __init__(self, status=206, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.content = body
        self.closed = False

    def iter_content(self, chunk_size=65536):
        yield self._body

    def json(self):
        import json
        return json.loads(self._body)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    def put(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_source_range_requires_exact_206_content_range_and_validator():
    response = FakeResponse(206, {
        "Content-Range": "bytes 4-7/20", "ETag": '"source-v1"'
    }, b"abcd")
    session = FakeSession(response)
    data, total, validator = upload._read_source_range(
        session, "https://archive.org/download/item/game.pkg", 4, 4
    )
    assert data == b"abcd"
    assert total == 20
    assert validator == ("ETag", '"source-v1"')
    assert response.closed
    assert session.calls[0][1]["headers"]["Range"] == "bytes=4-7"


def test_source_range_sends_if_range_and_rejects_changed_validator():
    response = FakeResponse(206, {
        "Content-Range": "bytes 4-7/20", "ETag": '"source-v2"'
    }, b"abcd")
    session = FakeSession(response)
    with pytest.raises(upload.UploadError, match="representation changed"):
        upload._read_source_range(
            session, "https://archive.org/download/item/game.pkg", 4, 4,
            expected_validator=("ETag", '"source-v1"'),
        )
    assert session.calls[0][1]["headers"]["If-Range"] == '"source-v1"'


@pytest.mark.parametrize(
    "status,content_range,headers",
    [
        (200, "bytes 0-3/4", {"ETag": '"v1"'}),
        (206, "bytes 1-4/10", {"ETag": '"v1"'}),
        (206, "", {"ETag": '"v1"'}),
        (206, "bytes 0-3/4", {}),
    ],
)
def test_source_range_fails_closed(status, content_range, headers):
    response = FakeResponse(status, {"Content-Range": content_range, **headers}, b"abcd")
    with pytest.raises(upload.UploadError):
        upload._read_source_range(
            FakeSession(response),
            "https://archive.org/download/item/game.pkg",
            0,
            4,
        )


def test_source_host_allowlist_rejects_lookalikes():
    assert upload._source_host_allowed("https://archive.org/file.pkg")
    assert upload._source_host_allowed("https://ia800000.us.archive.org/file.pkg")
    assert not upload._source_host_allowed("https://archive.org.attacker.example/file.pkg")
    assert not upload._source_host_allowed("http://archive.org/file.pkg")


def test_mocha_storage_host_allowlist_rejects_untrusted_host():
    assert upload._mocha_storage_url_allowed("https://stor11.mochaprox.li/put?signature=x")
    assert not upload._mocha_storage_url_allowed("https://attacker.example/put")
    assert not upload._mocha_storage_url_allowed("http://stor11.mochaprox.li/put")


def test_put_part_requires_etag():
    response = FakeResponse(200, {}, b"")
    session = FakeSession(response)
    with pytest.raises(upload.UploadError, match="omitted ETag"):
        upload._put_part(
            session, "https://stor11.mochaprox.li/part", b"part-data", 1
        )


def test_remote_pkg_upload_uses_exact_source_bytes_and_returns_file_id(monkeypatch):
    api_calls = []
    put_calls = []
    source = bytes.fromhex("7f434e54") + b"H" * (upload.MIN_PART_BYTES + 17 - 4)
    validator = ("ETag", '"stable-source"')

    def fake_range(_session, _url, start, length, *, expected_validator=None, overall_deadline=None):
        if expected_validator is not None:
            assert expected_validator == validator
        return source[start:start + length], len(source), validator

    def fake_api(_session, method, path, _key, body=None, params=None, deadline=None):
        api_calls.append((method, path, body))
        if path == "/files/multipart/init":
            return {
                "uploadId": "upload-1",
                "key": "object-key",
                "nodeId": "node-1",
                "originalName": body["originalName"],
                "partSizeBytes": upload.MIN_PART_BYTES,
            }
        if path == "/files/multipart/presigned":
            number = body["partNumbers"][0]
            return {"urls": [{"partNumber": number,
                              "url": "https://stor11.mochaprox.li/part"}]}
        if path == "/files/multipart/complete":
            assert body["originalName"] == api_calls[0][2]["originalName"]
            return {"file": {"id": "mocha-file-123"}}
        if path == "/files/mocha-file-123":
            assert method == "PATCH"
            assert body == {"originalName": "game.pkg"}
            return {}
        raise AssertionError(path)

    def fake_put(_session, url, data, part_number):
        put_calls.append((url, data, part_number))
        return f"etag-{part_number}"

    monkeypatch.setattr(upload, "_read_source_range", fake_range)
    monkeypatch.setattr(upload, "_request_json", fake_api)
    monkeypatch.setattr(upload, "_put_part", fake_put)

    result = upload.upload_remote_pkg(
        "https://archive.org/download/item/game.pkg",
        api_key="test-key",
        remote_path="/PS4/DLC",
        session=object(),
    )

    assert result == {
        "file_id": "mocha-file-123",
        "filename": "game.pkg",
        "size": len(source),
        "remote_path": "/PS4/DLC",
        "parts": 2,
    }
    assert [item[2] for item in put_calls] == [1, 2]
    assert [item[1] for item in put_calls] == [
        source[:upload.MIN_PART_BYTES],
        source[upload.MIN_PART_BYTES:],
    ]
    assert api_calls[0][2]["originalName"].startswith("fpkgi-upload-")
    assert api_calls[0][2]["originalName"].endswith(".pkg")
    assert [call[2]["parts"] for call in api_calls if call[1].endswith("/complete")] == [[
        {"partNumber": 1, "etag": "etag-1"},
        {"partNumber": 2, "etag": "etag-2"},
    ]]


def test_remote_pkg_upload_rejects_mismatched_presigned_part_and_aborts(monkeypatch):
    paths = []

    def fake_range(_session, _url, start, length, *, expected_validator=None):
        source = bytes.fromhex("7f434e54") + b"H" * (0x100 - 4)
        return source[start:start + length], len(source), ("ETag", '"v1"')

    def fake_api(_session, method, path, _key, body=None, params=None):
        paths.append(path)
        if path == "/files/multipart/init":
            return {"uploadId": "u", "key": "k", "nodeId": "n",
                    "partSizeBytes": upload.MIN_PART_BYTES}
        if path == "/files/multipart/presigned":
            return {"urls": [{"partNumber": 99,
                              "url": "https://stor11.mochaprox.li/part"}]}
        if path == "/files/multipart/abort":
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(upload, "_read_source_range", fake_range)
    monkeypatch.setattr(upload, "_request_json", fake_api)

    with pytest.raises(upload.UploadError, match="mismatched presigned part"):
        upload.upload_remote_pkg(
            "https://archive.org/download/item/game.pkg",
            api_key="test-key",
            remote_path="/PS4/DLC",
            session=object(),
        )
    assert paths[-1] == "/files/multipart/abort"


def test_remote_pkg_upload_fails_closed_on_ambiguous_completion(monkeypatch):
    paths = []
    source = bytes.fromhex("7f434e54") + b"H" * (0x100 - 4)

    def fake_range(_session, _url, start, length, *, expected_validator=None):
        return source[start:start + length], len(source), ("ETag", '"v1"')

    def fake_api(_session, method, path, _key, body=None, params=None):
        paths.append(path)
        if path == "/files/multipart/init":
            return {"uploadId": "u", "key": "k", "nodeId": "n",
                    "partSizeBytes": upload.MIN_PART_BYTES}
        if path == "/files/multipart/presigned":
            return {"urls": [{"partNumber": 1,
                              "url": "https://stor11.mochaprox.li/part"}]}
        if path == "/files/multipart/complete":
            raise TimeoutError("response lost after commit")
        if path == "/files":
            staged = params["q"]
            return {"files": [{
                "id": "recovered-file-1",
                "original_name": staged,
                "file_name": staged,
                "path": "/PS4/DLC",
                "size": len(source),
            }], "hasMore": False}
        if path == "/files/recovered-file-1":
            assert method == "PATCH"
            assert body == {"originalName": "game.pkg"}
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(upload, "_read_source_range", fake_range)
    monkeypatch.setattr(upload, "_request_json", fake_api)
    monkeypatch.setattr(upload, "_put_part", lambda *_args: "etag-1")

    result = upload.upload_remote_pkg(
        "https://archive.org/download/item/game.pkg",
        api_key="test-key",
        remote_path="/PS4/DLC",
        session=object(),
    )
    assert result["file_id"] == "recovered-file-1"
    assert paths.count("/files/multipart/complete") == 1
    assert paths.count("/files") == 1
    assert "/files/recovered-file-1" in paths
    assert "/files/multipart/abort" not in paths