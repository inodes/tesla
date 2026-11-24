"""test_card_cover.py - a card's picture comes from the first minute whose video gave a picture: a first clip a drive cut short (unreadable.json "stamp|...") is passed over.

    python3 -m unittest tests/test_card_cover.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TESLA_PERF_LOG", "")
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_teslacam as AT  # noqa: E402


class Cover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tc = object.__new__(AT.TeslaCam)
        self.tc.dir = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, d):
        os.makedirs(os.path.join(self.tmp, "teslacam_stills"), exist_ok=True)
        with open(os.path.join(self.tmp, "teslacam_stills", "unreadable.json"), "w") as f:
            json.dump(d, f)

    def test_the_minutes_that_gave_no_picture_are_the_stamp_keys_only(self):
        self.assertEqual(self.tc._dud_stamps(), set())                                       # no file: none
        self.write({"J|RecentClips/2026-11-24_15-50-10-front.mp4": 131072, "stamp|2026-11-24_15-50-10": 131072})
        self.assertEqual(self.tc._dud_stamps(), {"2026-11-24_15-50-10"})

    def test_a_changed_file_is_read_again(self):
        self.write({"stamp|a": 1})
        self.assertEqual(self.tc._dud_stamps(), {"a"})
        self.write({"stamp|a": 1, "stamp|b": 1})
        os.utime(os.path.join(self.tmp, "teslacam_stills", "unreadable.json"), (1, 2 + os.path.getmtime(self.tmp)))
        self.assertEqual(self.tc._dud_stamps(), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
