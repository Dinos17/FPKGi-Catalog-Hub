from tools import url_to_hf


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.headers = {}

    def get(self, url, timeout=None):
        return FakeResponse({"part-0": b"abc", "part-1": b"de"}[url])


def test_staged_composite_reads_across_part_boundaries(monkeypatch):
    monkeypatch.setattr(url_to_hf, "hf_hub_url", lambda **kwargs: kwargs["filename"])
    monkeypatch.setattr(url_to_hf.requests, "Session", FakeSession)

    stream = url_to_hf.StagedCompositeFile(
        repo_id="owner/dataset",
        repo_type="dataset",
        paths=["part-0", "part-1"],
        sizes=[3, 2],
        token="test-token",
        total_size=5,
    )

    assert stream.read(2) == b"ab"
    assert stream.tell() == 2
    assert stream.read(2) == b"cd"
    assert stream.read(10) == b"e"
    assert stream.read(1) == b""

    stream.seek(1)
    assert stream.read(3) == b"bcd"


def test_staged_composite_supports_seek_from_end(monkeypatch):
    monkeypatch.setattr(url_to_hf, "hf_hub_url", lambda **kwargs: kwargs["filename"])
    monkeypatch.setattr(url_to_hf.requests, "Session", FakeSession)

    stream = url_to_hf.StagedCompositeFile(
        repo_id="owner/dataset",
        repo_type="dataset",
        paths=["part-0", "part-1"],
        sizes=[3, 2],
        token="test-token",
        total_size=5,
    )

    assert stream.seek(-2, 2) == 3
    assert stream.read(2) == b"de"



def test_staged_composite_slice_reads_only_its_range():
    base = __import__("io").BytesIO(b"abcdefghij")
    stream = url_to_hf.StagedCompositeSlice(base, start=3, length=4)

    assert stream.read(2) == b"de"
    assert stream.tell() == 2
    assert stream.read(20) == b"fg"
    assert stream.read(1) == b""
    assert stream.seek(-2, 2) == 2
    assert stream.read() == b"fg"


def test_staged_composite_slice_rejects_out_of_bounds_seek():
    base = __import__("io").BytesIO(b"abcdefghij")
    stream = url_to_hf.StagedCompositeSlice(base, start=3, length=4)

    try:
        stream.seek(5)
    except ValueError as exc:
        assert "outside staged slice" in str(exc)
    else:
        raise AssertionError("expected out-of-bounds seek to fail")
