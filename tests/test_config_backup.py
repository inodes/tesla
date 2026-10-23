"""test_config_backup.py - dated copies of config.json: made once per change, the oldest moved (never deleted) to archive/ and logged.

    python3 -m unittest tests/test_config_backup.py
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import config_backup as CB  # noqa: E402


class Backup(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = os.path.join(self.d, "config.json")

    def write(self, text):
        with open(self.cfg, "w") as f:
            f.write(text)

    def test_one_copy_per_change(self):
        self.write("{}")
        self.assertTrue(CB.backup(self.cfg))
        self.assertIsNone(CB.backup(self.cfg))                  # unchanged: no second copy
        self.write('{"a": 1}')
        self.assertTrue(CB.backup(self.cfg))
        self.assertEqual(len(CB.copies(self.cfg)), 2)

    def test_old_copies_are_moved_and_logged_never_deleted(self):
        for i in range(5):
            self.write('{"n": %d}' % i)
            CB.backup(self.cfg, keep=3)
            time.sleep(0.01)
        self.assertEqual(len(CB.copies(self.cfg)), 3)
        arch = os.path.join(CB.folder(self.cfg), "config.json.older")
        self.assertEqual(len(os.listdir(arch)), 2)              # 5 made = 3 kept + 2 archived
        with open(os.path.join(CB.folder(self.cfg), "archive_log.md")) as f:
            self.assertEqual(len([ln for ln in f.read().splitlines() if ln.startswith("- ")]), 2)    # one line per moved copy

    def test_missing_config_does_nothing(self):
        self.assertIsNone(CB.backup(os.path.join(self.d, "nope.json")))


if __name__ == "__main__":
    unittest.main()
