#!/usr/bin/env python3
"""test_exam_teslacam.py - the marking of the clips-only cutting against Tessie's drives (lib/exam_teslacam.py), made-up times only.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_exam_teslacam.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import exam_teslacam as EX  # noqa: E402

MIN = list(range(0, 3600, 60))          # an hour of minutes the clips say something about


class ExamTest(unittest.TestCase):
    def test_a_drive_the_clips_cut_the_same_way_is_ok(self):
        r = EX.mark([("park", 0, 600), ("drive", 600, 1200), ("park", 1200, 3600)], [dict(t0=630, t1=1170)], MIN)
        self.assertEqual((len(r["ok"]), len(r["split"]), len(r["merged"]), len(r["missed"])), (1, 0, 0, 0))

    def test_one_clip_drive_over_two_of_tessies_is_merged_and_two_over_one_is_split(self):
        r = EX.mark([("drive", 600, 1800)], [dict(t0=630, t1=1000), dict(t0=1100, t1=1770)], MIN)
        self.assertEqual(len(r["merged"]), 2)
        r = EX.mark([("drive", 600, 1000), ("drive", 1000, 1800)], [dict(t0=630, t1=1770)], MIN)
        self.assertEqual(len(r["split"]), 1)

    def test_a_drive_the_clips_call_parked_is_missed_and_one_the_clips_do_not_cover_is_not_marked(self):
        r = EX.mark([("park", 0, 3600)], [dict(t0=630, t1=1170)], MIN)
        self.assertEqual(len(r["missed"]), 1)
        r = EX.mark([("park", 0, 600)], [dict(t0=630, t1=1170)], [x for x in MIN if x < 600])
        self.assertEqual((len(r["unchecked"]), len(r["missed"])), (1, 0))

    def test_a_clip_drive_with_no_tessie_drive_is_extra_but_a_hop_is_not(self):
        self.assertEqual(len(EX.mark([("drive", 600, 1200)], [], MIN)["extra"]), 1)
        self.assertEqual(len(EX.mark([("drive", 600, 650)], [], MIN)["extra"]), 0)

    def test_edges_further_off_than_a_clip_are_not_ok(self):
        r = EX.mark([("drive", 600, 1200)], [dict(t0=900, t1=1170)], MIN)
        self.assertEqual((len(r["ok"]), len(r["split"])), (0, 1))


if __name__ == "__main__":
    unittest.main()
