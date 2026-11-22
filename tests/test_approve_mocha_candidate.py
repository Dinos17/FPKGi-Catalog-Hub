from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.approve_mocha_candidate import apply_candidate, CATALOG_PATHS

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


class ApproveMochaCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for (platform, category), relative in CATALOG_PATHS.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"DATA": {"https://example.invalid/dead-rising.pkg": dict(METADATA)}}), encoding="utf-8")
        self.catalog_path = "ps4/games.json"
        self.matched_key = "https://example.invalid/dead-rising.pkg"
        self.candidate_id = hashlib.sha256(
            (FILE_ID + "\0" + COMMIT + "\0" + self.catalog_path + "\0" + self.matched_key).encode("utf-8")
        ).hexdigest()[:24]
        self.candidate = {
            "candidate_id": self.candidate_id,
            "file_id": FILE_ID,
            "name": "DEAD RISING - [US] [EN] [1.00].pkg",
            "created_at": "2026-11-22T09:00:00Z",
            "platform": "PS4",
            "category": "games",
            "catalog_path": self.catalog_path,
            "matched_catalog_key": self.matched_key,
            "catalog_metadata": dict(METADATA),
            "proposed_entry": {**METADATA, "download_source": "mocha"},
            "match_status": "matched",
            "metadata_differences": [],
            "binary_integrity_verified": False,
            "approval_required": True,
        }
        self.queue = {
            "schema_version": 1,
            "scanner": "mocha",
            "scan_observed_at": "2026-11-22T10:00:00Z",
            "catalog_commit": COMMIT,
            "candidate_count": 1,
            "candidates": [self.candidate],
            "catalog_write_performed": False,
            "automatic_approval": False,
            "binary_integrity_verified": False,
        }
        self.summary = {
            "schema_version": 1,
            "scanner": "mocha",
            "scan_complete": True,
            "scan_observed_at": self.queue["scan_observed_at"],
            "catalog_commit": COMMIT,
        }

    def tearDown(self):
        self.temp.cleanup()

    def apply_one(self):
        return apply_candidate(self.queue, self.summary, self.candidate_id, self.root, COMMIT)

    def test_valid_candidate_adds_mocha_data_key_and_preserves_metadata(self):
        path, name = self.apply_one()
        self.assertEqual(path, self.catalog_path)
        self.assertEqual(name, self.candidate["name"])
        payload = json.loads((self.root / path).read_text(encoding="utf-8"))
        self.assertEqual(payload["DATA"][FILE_ID], {**METADATA, "download_source": "mocha"})
        self.assertEqual(payload["DATA"][self.matched_key], METADATA)

    def test_stale_catalog_commit_rejected(self):
        with self.assertRaises(ValueError):
            apply_candidate(self.queue, self.summary, self.candidate_id, self.root, "b" * 40)

    def test_summary_mismatch_rejected(self):
        self.summary["scan_observed_at"] = "2026-11-23T10:00:00Z"
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_unknown_candidate_rejected(self):
        with self.assertRaises(ValueError):
            apply_candidate(self.queue, self.summary, "f" * 24, self.root, COMMIT)

    def test_duplicate_candidate_identity_rejected(self):
        self.queue["candidates"].append(dict(self.candidate))
        self.queue["candidate_count"] = 2
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_tampered_proposal_rejected(self):
        self.candidate["proposed_entry"]["name"] = "TAMPERED"
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_changed_matched_catalog_entry_rejected(self):
        path = self.root / self.catalog_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["DATA"][self.matched_key]["name"] = "CHANGED"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_duplicate_file_id_in_candidate_report_rejected(self):
        duplicate = dict(self.candidate)
        duplicate["candidate_id"] = "f" * 24
        self.queue["candidates"].append(duplicate)
        self.queue["candidate_count"] = 2
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_already_mocha_backed_catalog_entry_rejected(self):
        path = self.root / self.catalog_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["DATA"][self.matched_key]["download_source"] = "mocha"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.candidate["catalog_metadata"]["download_source"] = "mocha"
        self.candidate["proposed_entry"]["download_source"] = "mocha"
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_candidate_name_newlines_are_sanitized(self):
        self.candidate["name"] = "DEAD RISING\n::set-output name=evil::true"
        _, name = self.apply_one()
        self.assertNotIn("\n", name)
        self.assertNotIn("\r", name)

    def test_existing_mocha_file_id_rejected(self):
        path = self.root / "ps4/dlc.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["DATA"][FILE_ID] = {"download_source": "mocha"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_invalid_platform_target_rejected(self):
        self.candidate["catalog_path"] = "../../README.md"
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_invalid_file_id_rejected(self):
        self.candidate["file_id"] = "../../README.md"
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_candidate_id_tampering_rejected(self):
        self.candidate["file_id"] = "other-id"
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_binary_integrity_must_remain_unverified(self):
        self.candidate["binary_integrity_verified"] = True
        with self.assertRaises(ValueError):
            self.apply_one()

    def test_naive_upload_timestamp_rejected(self):
        self.candidate["created_at"] = "2026-11-22T09:00:00"
        with self.assertRaises(ValueError):
            self.apply_one()


if __name__ == "__main__":
    unittest.main()
