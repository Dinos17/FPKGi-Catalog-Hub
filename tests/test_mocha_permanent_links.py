from __future__ import annotations

from tools.mocha_permanent_links import (
    MochaPermanentLinkClient,
    PermanentLinkError,
    create_links,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload


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
    session = FakeSession([success("file-123")])
    client = MochaPermanentLinkClient("test-key", session)

    result = client.create("file-123")

    url, request = session.calls[0]
    assert url == "https://api.mocha.my/api/permanent-links"
    assert request["json"] == {"fileId": "file-123"}
    assert request["allow_redirects"] is False
    assert result == {
        "file_id": "file-123",
        "token": "sample-token",
        "is_permanent_link": True,
        "creation_confirmed": True,
    }


def test_rejects_response_for_different_file():
    client = MochaPermanentLinkClient(
        "test-key", FakeSession([success("different-file")])
    )
    try:
        client.create("file-123")
    except PermanentLinkError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("mismatched file ID must be rejected")


def test_rejects_response_without_permanent_confirmation():
    response = FakeResponse({"link": {
        "token": "sample-token",
        "file_id": "file-123",
        "is_permanent_link": False,
    }})
    client = MochaPermanentLinkClient("test-key", FakeSession([response]))
    try:
        client.create("file-123")
    except PermanentLinkError as exc:
        assert "did not confirm" in str(exc)
    else:
        raise AssertionError("unconfirmed link must be rejected")


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
