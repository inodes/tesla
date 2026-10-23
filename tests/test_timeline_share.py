"""test_timeline_share.py - the share of a drive on FSD / Autosteer is by distance, counts only the car moving, and is never above 100%.

    python3 -m unittest tests/test_timeline_share.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import teslacam_timeline as TL  # noqa: E402


class ShareTest(unittest.TestCase):
    def test_by_distance_when_stored(self):
        sums = [dict(fsdk=1.0), dict(fsdk=0.0)]
        self.assertEqual(TL._share(sums, "fsd", "fsdk", 4.0, 999, 10), 0.25)

    def test_never_above_one(self):
        self.assertEqual(TL._share([dict(fsdk=5.0)], "fsd", "fsdk", 4.0, 0, 10), 1.0)

    def test_older_minutes_use_moving_seconds(self):
        self.assertEqual(TL._share([dict()], "fsd", "fsdk", 4.0, 30, 60), 0.5)
        self.assertEqual(TL._share([dict()], "fsd", "fsdk", 4.0, 90, 60), 1.0)
        self.assertEqual(TL._share([dict()], "fsd", "fsdk", 0, 5, 0), 0)


if __name__ == "__main__":
    unittest.main()
