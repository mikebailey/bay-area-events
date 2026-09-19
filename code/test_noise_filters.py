"""Regression tests for pseudo-events that must never reach the site."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources.base import is_noise  # noqa: E402


class NoiseFilterTests(unittest.TestCase):
    def test_closed_cancelled_and_rescheduled_titles_are_noise(self):
        for title in (
            "Venue Closed Today",
            "Theater Closed for Maintenance",
            "Private Event Tonight",
            "Cancelled: Friday Concert",
            "Canceled Performance",
            "Rescheduled to October",
        ):
            with self.subTest(title=title):
                self.assertTrue(is_noise(title))

    def test_word_boundaries_do_not_hide_real_events(self):
        for title in (
            "Private Eventide Garden Tour",
            "The Eventide Garden Tour",
        ):
            with self.subTest(title=title):
                self.assertFalse(is_noise(title))


if __name__ == "__main__":
    unittest.main()
