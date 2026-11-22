from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.auto_apply_mocha_candidates import apply_automatic_candidates
from tools.mocha_catalog_candidates import build_candidate_queue

COMMIT = "a" * 40
FILE_ID = "d5ac3913-802b-4d68-9803-8f5c07c7640e"
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


class AutomaticMochaRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for path in ("ps4/games.json", "ps4/dlc.json", "ps5/ps5-games.json", "ps5/ps5-dlc.json"):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            data = {"DATA": {"https://example.invalid/dead-rising.pkg": dict(METADATA)}}
            target.write_text(json.dumps(data), encoding="utf-8")
        self.catalog_path = "ps4/games.json"
        self.catalog_key = "https://example.invalid/dead-rising.pkg"
        self.scan = {
            "scanner": "mocha",
            "scan_complete": True,
            "catalog_write_performed": False,
            "scan_observed_at": "2026-11-22T10:00:00Z",
            "files": [{
                "id": FILE_ID,
                "name": "DEAD RISING - [US] [EN] [1.00].pkg",
                "extension": "pkg",
                "platform": "PS4",
                "catalog_category": "games",
                "created_at": "2026-11-22T09:00:00Z",
            }],
        }
        self.comparison = {
            "comparison": "mocha_catalog",
            "scan_complete": True,
            "catalog_commit": COMMIT,
            "results": [{
                "status": "matched",
                "file_id": FILE_ID,
                "name": self.scan["files"][0]["name"],
                "platform": "PS4",
                "category": "games",
                "catalog_url": self.catalog_key,
                "catalog_metadata": dict(METADATA),
                "metadata_differences": [],
                "unavailable_metadata": [],
                "source_metadata": {
                    "title": "DEAD RISING",
                    "title_id": "CUSA04513",
                    "region": "USA",
                    "version": "01.00",
                    "size_bytes": METADATA["size"],
                },
            }],
            "possible_duplicates": [],
        }
        self.queue = build_candidate_queue(self.scan, self.comparison, self.root)
        self.candidate = self.queue["candidates"][0]
        self.summary = {
            "schema_version": 1,
            "scanner": "mocha",
            "scan_complete": True,
            "scan_observed_at": self.queue["scan_observed_at"],
            "catalog_commit": COMMIT,
        }
        self.decisions = {"schema_version": 1, "records": []}
        self.rejections = {"schema_version": 1, "records": []}

    def tearDown(self):
        self.temp.cleanup()

    def apply_batch(self):
        return apply_automatic_candidates(
            self.queue, self.summary, self.scan, self.comparison, self.root, COMMIT,
            self.decisions, self.rejections
        )

    def test_exact_unique_candidate_is_applied_without_human_action(self):
        result = self.apply_batch()
        self.assertEqual(result, [(self.catalog_path, self.candidate["name"])])
        payload = json.loads((self.root / self.catalog_path).read_text(encoding="utf-8"))
        self.assertEqual(payload["DATA"][FILE_ID], {**METADATA, "download_source": "mocha"})
        self.assertEqual(payload["DATA"][self.catalog_key], METADATA)

    def test_duplicate_catalog_target_fails_closed_before_write(self):
        duplicate = dict(self.candidate)
        duplicate["candidate_id"] = "f" * 24
        duplicate["file_id"] = "another-file-id"
        self.queue["candidates"].append(duplicate)
        self.queue["candidate_count"] = 2
        with self.assertRaises(ValueError):
            self.apply_batch()
        payload = json.loads((self.root / self.catalog_path).read_text(encoding="utf-8"))
        self.assertNotIn(FILE_ID, payload["DATA"])

    def test_previously_rejected_upload_is_not_auto_registered(self):
        self.rejections = {
            "schema_version": 1,
            "records": [{
                "status": "manual_deletion_required",
                "platform": "PS4",
                "catalog_category": "games",
                "name": self.scan["files"][0]["name"],
                "rejected_at": "2026-11-23T00:00:00Z",
            }],
        }
        self.queue = build_candidate_queue(
            self.scan, self.comparison, self.root, {FILE_ID}
        )
        self.assertEqual(self.apply_batch(), [])
        payload = json.loads((self.root / self.catalog_path).read_text(encoding="utf-8"))
        self.assertNotIn(FILE_ID, payload["DATA"])

    def test_stale_scan_fails_closed(self):
        with self.assertRaises(ValueError):
            apply_automatic_candidates(
                self.queue, self.summary, self.scan, self.comparison, self.root, "b" * 40,
                self.decisions, self.rejections
            )

    def test_forged_published_candidate_is_rejected_against_raw_scan(self):
        self.candidate["file_id"] = "attacker-controlled-id"
        with self.assertRaises(ValueError):
            self.apply_batch()
        payload = json.loads((self.root / self.catalog_path).read_text(encoding="utf-8"))
        self.assertNotIn("attacker-controlled-id", payload["DATA"])

    def test_binary_integrity_is_never_claimed(self):
        result = self.apply_batch()
        self.assertEqual(len(result), 1)
        self.assertFalse(self.queue["binary_integrity_verified"])


if __name__ == "__main__":
    unittest.main()
