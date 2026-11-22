from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.mocha_catalog_candidates import build_candidate_queue
from tools.mocha_review_queue import can_silently_clear_match, near_duplicate_file_ids


CATALOG_PATHS = {
    ("PS4", "games"): "ps4/games.json",
    ("PS4", "dlc"): "ps4/dlc.json",
    ("PS5", "games"): "ps5/ps5-games.json",
    ("PS5", "dlc"): "ps5/ps5-dlc.json",
}
COMMIT = "a" * 40
METADATA = {
    "title_id": "CUSA04513",
    "region": "USA",
    "name": "DEAD RISING",
    "version": "01.00",
    "release": "11-15-2013",
    "size": 8049262592,
    "min_fw": "9.00",
    "cover_url": "https://example.invalid/cover.jpg",
}


class MochaCatalogCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for (platform, category), relative in CATALOG_PATHS.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"DATA": {"https://example.invalid/dead-rising.pkg": dict(METADATA)}}
            path.write_text(json.dumps(data), encoding="utf-8")
        self.scan = {
            "scanner": "mocha",
            "scan_complete": True,
            "scan_observed_at": "2026-11-22T10:00:00Z",
            "files": [{
                "id": "d5ac3913-802b-4d68-9803-8f5c07c7640e",
                "name": "DEAD RISING - [US] [EN] [1.00].pkg",
                "extension": "pkg",
                "platform": "PS4",
                "catalog_category": "games",
                "created_at": "2026-11-22T09:00:00Z",
            }],
        }
        self.row = {
            "status": "matched",
            "file_id": self.scan["files"][0]["id"],
            "name": self.scan["files"][0]["name"],
            "platform": "PS4",
            "category": "games",
            "catalog_url": "https://example.invalid/dead-rising.pkg",
            "catalog_metadata": dict(METADATA),
            "metadata_differences": [],
            "unavailable_metadata": ["languages"],
            "source_metadata": {
                "title": "DEAD RISING",
                "region": "USA",
                "version": "01.00",
                "size_bytes": METADATA["size"],
            },
        }
        self.comparison = {
            "comparison": "mocha_catalog",
            "scan_complete": True,
            "catalog_commit": COMMIT,
            "results": [self.row],
            "possible_duplicates": [],
        }

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        return build_candidate_queue(self.scan, self.comparison, self.root)

    def test_exact_unique_match_generates_mocha_source_proposal(self):
        result = self.build()
        self.assertEqual(result["candidate_count"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["file_id"], self.scan["files"][0]["id"])
        self.assertEqual(candidate["proposed_entry"]["download_source"], "mocha")
        self.assertEqual(candidate["proposed_entry"]["title_id"], "CUSA04513")
        self.assertFalse(candidate["approval_required"])
        self.assertFalse(candidate["binary_integrity_verified"])
        self.assertFalse(result["catalog_write_performed"])

    def test_non_matched_rows_are_not_candidates(self):
        self.row["status"] = "metadata_difference"
        self.assertEqual(self.build()["candidate_count"], 0)

    def test_previously_decided_upload_is_not_candidate(self):
        file_id = self.row["file_id"]
        result = build_candidate_queue(self.scan, self.comparison, self.root, {file_id})
        self.assertEqual(result["candidate_count"], 0)

    def test_possible_duplicate_is_not_candidate(self):
        self.comparison["possible_duplicates"] = [{"file_ids": [self.row["file_id"]]}]
        self.assertEqual(self.build()["candidate_count"], 0)

    def test_near_duplicate_is_not_candidate(self):
        other = dict(self.row)
        other["file_id"] = "another-file-id"
        self.comparison["results"].append(other)
        self.assertEqual(self.build()["candidate_count"], 0)

    def test_existing_mocha_file_id_is_not_candidate(self):
        path = self.root / "ps4/games.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["DATA"][self.row["file_id"]] = {"download_source": "mocha"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self.build()["candidate_count"], 0)

    def test_existing_mocha_catalog_source_is_not_candidate(self):
        path = self.root / "ps4/games.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["DATA"]["https://example.invalid/dead-rising.pkg"]["download_source"] = "mocha"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.row["catalog_metadata"]["download_source"] = "mocha"
        self.assertEqual(self.build()["candidate_count"], 0)

    def test_multiple_mocha_uploads_for_same_catalog_record_are_not_auto_candidates(self):
        second_item = dict(self.scan["files"][0])
        second_item["id"] = "another-file-id"
        second_item["name"] = "DEAD RISING - [EU] [EN] [1.00].pkg"
        self.scan["files"].append(second_item)
        second_row = dict(self.row)
        second_row["file_id"] = second_item["id"]
        second_row["name"] = second_item["name"]
        second_row["source_metadata"] = dict(self.row["source_metadata"], region="EUR")
        self.comparison["results"].append(second_row)
        duplicate_ids = near_duplicate_file_ids(self.comparison["results"])
        self.assertEqual(duplicate_ids, {self.row["file_id"], second_row["file_id"]})
        result = self.build()
        self.assertEqual(result["candidate_count"], 0)

    def test_incomplete_matched_upload_identity_stays_in_human_review(self):
        self.assertFalse(can_silently_clear_match(
            "matched", False, False, identity_complete=False
        ))

    def test_inconsistent_catalog_metadata_fails_closed(self):
        self.row["catalog_metadata"]["name"] = "TAMPERED"
        with self.assertRaises(ValueError):
            self.build()

    def test_missing_catalog_entry_fails_closed(self):
        self.row["catalog_url"] = "https://example.invalid/missing.pkg"
        with self.assertRaises(ValueError):
            self.build()

    def test_incomplete_scan_fails_closed(self):
        self.scan["scan_complete"] = False
        with self.assertRaises(ValueError):
            self.build()

    def test_invalid_catalog_commit_fails_closed(self):
        self.comparison["catalog_commit"] = "not-a-commit"
        with self.assertRaises(ValueError):
            self.build()

    def test_duplicate_scan_ids_fail_closed(self):
        self.scan["files"].append(dict(self.scan["files"][0]))
        with self.assertRaises(ValueError):
            self.build()

    def test_invalid_mocha_id_is_ignored(self):
        self.row["file_id"] = "bad/id"
        self.assertEqual(self.build()["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
