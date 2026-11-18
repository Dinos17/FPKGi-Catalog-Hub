import json
from datetime import datetime, timezone

import pytest

from tools.akirabox_rejections import parse_rejection_body, record_issue


def make_event(
    *,
    actor="Dinos17",
    title="AkiraBox rejection: Example Game",
    file_id="abc123",
    url="https://akirabox.to/abc123/file",
):
    payload = {
        "file_id": file_id,
        "name": "Example Game.pkg",
        "share_url": url,
        "scan_generated_at": "2026-11-17T23:53:20Z",
        "platform": "PS4",
        "catalog_category": "games",
    }
    body = (
        "Please record this rejected upload.\n\n"
        "<!-- akirabox-rejection:v1\n"
        + json.dumps(payload)
        + "\n-->"
    )
    return {
        "issue": {
            "number": 42,
            "title": title,
            "body": body,
            "state": "open",
            "html_url": "https://github.com/Dinos17/FPKGi-Catalog-Hub/issues/42",
            "user": {"login": actor},
        }
    }


def test_parse_rejection_body_accepts_akirabox_https_file_url():
    result = parse_rejection_body(make_event()["issue"]["body"])
    assert result["file_id"] == "abc123"
    assert result["share_url"] == "https://akirabox.to/abc123/file"


@pytest.mark.parametrize(
    "url",
    [
        "http://akirabox.to/abc123/file",
        "https://evil.example/abc123/file",
        "https://user:pass@akirabox.to/abc123/file",
    ],
)
def test_parse_rejection_body_rejects_unsafe_urls(url):
    event = make_event(url=url)
    with pytest.raises(ValueError):
        parse_rejection_body(event["issue"]["body"])


def test_only_expected_owner_can_record_rejection(tmp_path):
    with pytest.raises(ValueError, match="Only the repository owner"):
        record_issue(
            make_event(actor="someone-else"),
            tmp_path / "reports" / "akirabox-rejections.json",
            expected_actor="Dinos17",
        )


def test_rejection_is_persisted_and_duplicate_submission_is_idempotent(tmp_path):
    path = tmp_path / "reports" / "akirabox-rejections.json"
    fixed_now = datetime(2026, 11, 18, tzinfo=timezone.utc)

    record_issue(make_event(), path, "Dinos17", now=fixed_now)
    record_issue(make_event(), path, "Dinos17", now=fixed_now)

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert len(report["records"]) == 1
    assert report["records"][0]["status"] == "manual_deletion_required"
    assert report["records"][0]["issue_number"] == 42


def test_rejection_requires_expected_title_and_open_issue(tmp_path):
    for event in (
        make_event(title="Some unrelated issue"),
        {**make_event(), "issue": {**make_event()["issue"], "state": "closed"}},
    ):
        with pytest.raises(ValueError):
            record_issue(
                event,
                tmp_path / "rejections.json",
                expected_actor="Dinos17",
            )



def test_parser_handles_filename_containing_json_and_comment_delimiters():
    payload = {
        "file_id": "abc123",
        "name": "Demo} --> Trial.pkg",
        "share_url": "https://akirabox.to/abc123/file",
        "platform": "PS4",
        "catalog_category": "games",
    }
    body = "<!-- akirabox-rejection:v1\n" + json.dumps(payload) + "\n-->"
    assert parse_rejection_body(body)["name"] == "Demo} --> Trial.pkg"


def test_parser_requires_share_url_path_to_match_file_id():
    event = make_event(url="https://akirabox.to/different-id/file")
    with pytest.raises(ValueError, match="matching HTTPS"):
        parse_rejection_body(event["issue"]["body"])


def test_retry_same_issue_preserves_original_timestamp_and_provenance(tmp_path):
    path = tmp_path / "rejections.json"
    first = make_event()
    first["issue"]["number"] = 42
    first_now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    record_issue(first, path, "Dinos17", now=first_now)

    retry_now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    saved = record_issue(first, path, "Dinos17", now=retry_now)
    assert saved["rejected_at"] == "2026-11-18T00:00:00Z"
    assert saved["issue_number"] == 42


def test_older_issue_cannot_replace_newer_rejection_provenance(tmp_path):
    path = tmp_path / "rejections.json"
    newer = make_event()
    newer["issue"]["number"] = 43
    newer["issue"]["html_url"] = "https://github.com/Dinos17/FPKGi-Catalog-Hub/issues/43"
    record_issue(newer, path, "Dinos17", now=datetime(2026, 11, 19, tzinfo=timezone.utc))

    older = make_event()
    older["issue"]["number"] = 42
    older["issue"]["html_url"] = "https://github.com/Dinos17/FPKGi-Catalog-Hub/issues/42"
    saved = record_issue(older, path, "Dinos17", now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    assert saved["issue_number"] == 43
    assert saved["issue_url"].endswith("/43")


@pytest.mark.parametrize("terminator", ["-->", "--!>"])
def test_parser_accepts_valid_html_comment_terminators(terminator):
    event = make_event()
    body = event["issue"]["body"].rsplit("-->", 1)[0] + terminator
    assert parse_rejection_body(body)["file_id"] == "abc123"
