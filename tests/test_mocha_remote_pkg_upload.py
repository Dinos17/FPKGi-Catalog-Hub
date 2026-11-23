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


def test_source_range_requires_exact_206_and_content_range():
    body = b"abcd"
    response = FakeResponse(
        206, {"Content-Range": "bytes 4-7/20"}, body
    )
    session = FakeSession(response)
    data, total = upload._read_source_range(
        session, "https://archive.org/download/item/game.pkg", 4, 4
    )
    assert data == body
    assert total == 20
    assert response.closed
    assert session.calls[0][1]["headers"]["Range"] == "bytes=4-7"


@pytest.mark.parametrize(
    "status,content_range",
    [(200, "bytes 0-3/4"), (206, "bytes 1-4/10"), (206, "")],
)
def test_source_range_fails_closed(status, content_range):
    response = FakeResponse(status, {"Content-Range": content_range}, b"abcd")
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


def test_remote_pkg_upload_uses_multipart_and_returns_file_id(monkeypatch):
    api_calls = []
    put_calls = []

    def fake_range(_session, _url, start, length):
        if start == 0 and length == 1:
            return bytes.fromhex("7f434e54"), 8
        return (b"X" * length), 8

    def fake_api(_session, method, path, _key, body=None):
        api_calls.append((method, path, body))
        if path == "/files/multipart/init":
            return {
                "uploadId": "upload-1",
                "key": "object-key",
                "nodeId": "node-1",
                "originalName": "game.pkg",
                "partSizeBytes": 4,
            }
        if path == "/files/multipart/presigned":
            number = body["partNumbers"][0]
            return {"urls": [{"partNumber": number,
                              "url": "https://stor11.mochaprox.li/part"}]}
        if path == "/files/multipart/complete":
            return {"file": {"id": "mocha-file-123"}}
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
        "size": 8,
        "remote_path": "/PS4/DLC",
        "parts": 2,
    }
    assert [call[2]["parts"] for call in api_calls if call[1].endswith("/complete")] == [[
        {"partNumber": 1, "etag": "etag-1"},
        {"partNumber": 2, "etag": "etag-2"},
    ]]
    assert [item[2] for item in put_calls] == [1, 2]


def test_remote_pkg_upload_aborts_after_part_failure(monkeypatch):
    paths = []

    def fake_range(_session, _url, start, length):
        return (bytes.fromhex("7f434e54") if length == 4 else b"X" * length), 4

    def fake_api(_session, method, path, _key, body=None):
        paths.append(path)
        if path == "/files/multipart/init":
            return {"uploadId": "u", "key": "k", "nodeId": "n",
                    "partSizeBytes": 4}
        if path == "/files/multipart/presigned":
            return {"urls": [{"url": "https://stor11.mochaprox.li/part"}]}
        if path == "/files/multipart/abort":
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(upload, "_read_source_range", fake_range)
    monkeypatch.setattr(upload, "_request_json", fake_api)
    monkeypatch.setattr(
        upload, "_put_part",
        lambda *_args: (_ for _ in ()).throw(upload.UploadError("storage failure")),
    )

    with pytest.raises(upload.UploadError, match="storage failure"):
        upload.upload_remote_pkg(
            "https://archive.org/download/item/game.pkg",
            api_key="test-key",
            remote_path="/PS4/DLC",
            session=object(),
        )
    assert paths[-1] == "/files/multipart/abort"
