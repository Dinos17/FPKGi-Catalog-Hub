from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests

from tools.mocha_permanent_links import (
    MAX_RESPONSE_BYTES,
    MochaPermanentLinkClient,
    PermanentLinkError,
    _write_private_json,
    create_links,
)


class FakeResponse:
    def __init__(self, payload=None, status=200, chunks=None):
        self.payload = payload
        self.status_code = status
        self.chunks = chunks if chunks is not None else [
            json.dumps(payload).encode("utf-8")
        ]
        self.closed = False

    def iter_content(self, chunk_size=4096):
        yield from self.chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def success(file_id, token="sample-token"):
    return FakeResponse({
        "link": {
            "token": token,
            "file_id": file_id,
            "is_permanent_link": True,
        }
    })


def test_creates_permanent_link_with_documented_request_shape():
    response = success("file-123")
    session = FakeSession([response])
    client = MochaPermanentLinkClient("test-key", session)

    result = client.create("file-123")

    url, request = session.calls[0]
    assert url == "https://api.mocha.my/api/permanent-links"
    assert request["json"] == {"fileId": "file-123"}
    assert request["allow_redirects"] is False
    assert request["stream"] is True
    assert request["timeout"] == (5, 2)
    assert response.closed is True
    assert result == {
        "file_id": "file-123",
        "token": "sample-token",
        "is_permanent_link": True,
        "creation_confirmed": True,
    }


def test_rejects_missing_or_null_response_file_id():
    for response_id in (None,):
        response = FakeResponse({"link": {
            "token": "sample-token",
            "file_id": response_id,
            "is_permanent_link": True,
        }})
        client = MochaPermanentLinkClient("test-key", FakeSession([response]))
        with pytest.raises(PermanentLinkError, match="missing a valid file ID"):
            client.create("None")


def test_rejects_response_for_different_file():
    client = MochaPermanentLinkClient(
        "test-key", FakeSession([success("different-file")])
    )
    with pytest.raises(PermanentLinkError, match="does not match"):
        client.create("file-123")


def test_rejects_response_without_permanent_confirmation():
    response = FakeResponse({"link": {
        "token": "sample-token",
        "file_id": "file-123",
        "is_permanent_link": False,
    }})
    client = MochaPermanentLinkClient("test-key", FakeSession([response]))
    with pytest.raises(PermanentLinkError, match="did not confirm"):
        client.create("file-123")


def test_rejects_oversized_response_and_closes_it():
    response = FakeResponse(chunks=[b"x" * (MAX_RESPONSE_BYTES + 1)])
    client = MochaPermanentLinkClient("test-key", FakeSession([response]))
    with pytest.raises(PermanentLinkError, match="exceeded"):
        client.create("file-123")
    assert response.closed is True


def test_network_failure_is_marked_as_ambiguous():
    client = MochaPermanentLinkClient(
        "test-key", FakeSession([requests.ConnectionError("lost connection")])
    )
    with pytest.raises(PermanentLinkError) as error:
        client.create("file-123")
    assert error.value.outcome_unknown is True


def test_deduplicates_requested_ids_and_records_failures():
    session = FakeSession([success("file-1"), FakeResponse({}, status=403)])
    client = MochaPermanentLinkClient("test-key", session)

    report = create_links(["file-1", "file-1", "file-2"], client)

    assert len(session.calls) == 2
    assert report["created_count"] == 1
    assert report["failed_count"] == 1
    assert report["catalog_write_performed"] is False
    assert report["public_url_resolved"] is False
    assert [row["status"] for row in report["results"]] == ["created", "error"]


def test_persists_each_success_and_skips_it_on_later_invocations():
    session = FakeSession([success("file-1")])
    client = MochaPermanentLinkClient("test-key", session)
    state = {"files": {}}
    persisted = []

    first = create_links(
        ["file-1"], client, state=state,
        persist_state=lambda value: persisted.append(json.loads(json.dumps(value))),
    )
    second = create_links(["file-1"], client, state=state)

    assert first["created_count"] == 1
    assert second["results"][0]["status"] == "already_created"
    assert len(session.calls) == 1
    assert persisted[0]["files"]["file-1"]["token"] == "sample-token"


def test_unknown_outcome_is_persisted_and_never_retried_automatically():
    session = FakeSession([requests.Timeout("timeout")])
    client = MochaPermanentLinkClient("test-key", session)
    state = {"files": {}}
    first = create_links(["file-1"], client, state=state)
    second = create_links(["file-1"], client, state=state)

    assert first["results"][0]["status"] == "unknown"
    assert second["results"][0]["status"] == "unknown"
    assert len(session.calls) == 1


def test_private_json_writer_sets_owner_only_permissions(tmp_path: Path):
    path = tmp_path / "private.json"
    _write_private_json(path, {"token": "secret"})
    assert json.loads(path.read_text()) == {"token": "secret"}
    assert os.stat(path).st_mode & 0o777 == 0o600
