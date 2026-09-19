"""Tests for the dependency-free project .env loader."""

import os
import tempfile
import unittest
from pathlib import Path

from fetch import load_env


class LoadEnvTests(unittest.TestCase):
    def test_project_value_overrides_inherited_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("TICKETMASTER_API_KEY='project-key'\n", encoding="utf-8")
            old = os.environ.get("TICKETMASTER_API_KEY")
            os.environ["TICKETMASTER_API_KEY"] = "ambient-key"
            try:
                load_env(path)
                self.assertEqual(os.environ["TICKETMASTER_API_KEY"], "project-key")
            finally:
                if old is None:
                    os.environ.pop("TICKETMASTER_API_KEY", None)
                else:
                    os.environ["TICKETMASTER_API_KEY"] = old

    def test_comments_and_blank_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# comment\n\nDIGEST_TO='one@example.com,two@example.com'\n", encoding="utf-8")
            old = os.environ.get("DIGEST_TO")
            try:
                load_env(path)
                self.assertEqual(os.environ["DIGEST_TO"], "one@example.com,two@example.com")
            finally:
                if old is None:
                    os.environ.pop("DIGEST_TO", None)
                else:
                    os.environ["DIGEST_TO"] = old


if __name__ == "__main__":
    unittest.main()
