from __future__ import annotations

import unittest

from tools.mocha_decision_key import decision_key


class MochaDecisionKeyTests(unittest.TestCase):
    def test_javascript_compatible_known_vector(self):
        self.assertEqual(
            decision_key("PS4", "games", "DEAD RISING.pkg", "2026-11-22T09:00:00Z"),
            "1275a984007a0c1f67f27cba8116d0a1426c732d10911e8fd992b1477b82488b",
        )

    def test_equivalent_timezone_and_fraction_formats_match(self):
        expected = decision_key(
            "PS4", "games", "DEAD RISING.pkg", "2026-11-22T09:00:00Z"
        )
        self.assertEqual(
            decision_key("PS4", "games", "DEAD RISING.pkg", "2026-11-22T11:00:00+02:00"),
            expected,
        )
        self.assertEqual(
            decision_key("PS4", "games", "DEAD RISING.pkg", "2026-11-22T09:00:00.000000+00:00"),
            expected,
        )

    def test_unicode_nfkc_and_case_normalization(self):
        self.assertEqual(
            decision_key("PS4", "games", "  ＤＥＡＤ RISING.pkg  ", "2026-11-22T09:00:00Z"),
            decision_key("PS4", "games", "dead rising.pkg", "2026-11-22T09:00:00.000+00:00"),
        )

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            decision_key("PS4", "games", "DEAD RISING.pkg", "2026-11-22T09:00:00")


if __name__ == "__main__":
    unittest.main()
