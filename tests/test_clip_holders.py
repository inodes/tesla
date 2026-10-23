"""test_clip_holders.py - the footage log answers where a minute of video is (the black box of the player), with every drive away.

    python3 -m unittest tests/test_clip_holders.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import teslacam_ledger as L  # noqa: E402

ST = "2026-10-16_15-44-07"
LIVE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone=None)
GONE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone="2026-10-18T00:00:00Z")


class Holders(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, L.DIRNAME))
        day = {
            "recent||%s-back" % ST: dict(size=50, drives={"J": LIVE}),
            "recent||%s-front" % ST: dict(size=100, drives={"A": LIVE, "J": LIVE}),
            "recent||2026-10-16_15-45-07-front": dict(size=70, drives={"J": GONE}),
        }
        with open(os.path.join(d, L.DIRNAME, "2026-10-16.json"), "w") as f:
            json.dump(day, f)
        self.led = L.Ledger(d)

    def test_kept_front_first_with_its_drives(self):
        h = self.led.holders([ST])[ST]
        self.assertEqual(h["state"], "kept")
        self.assertEqual([f["cam"] for f in h["files"]], ["front", "back"])
        self.assertEqual(h["files"][0]["name"], ST + "-front.mp4")
        self.assertEqual(sorted(h["files"][0]["drives"]), ["A", "J"])

    def test_gone_from_every_drive_is_deleted(self):
        self.assertEqual(self.led.holders(["2026-10-16_15-45-07"])["2026-10-16_15-45-07"]["state"], "deleted")

    def test_never_logged_is_unknown(self):
        self.assertEqual(self.led.holders(["2026-10-16_15-46-07"])["2026-10-16_15-46-07"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
