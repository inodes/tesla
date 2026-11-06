#!/usr/bin/env python3
"""test_sign_frames.py - a sign keeps where it stood in every frame it was read in (metadata), the best frame (biggest ring, read most surely) is the one pointed at and the only one that keeps
a picture, at the full size of the frame (crop_px). The video's ring is drawn from that.

    python3 -m unittest tests/test_sign_frames.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
import app_review as R  # noqa: E402


def obs(s, text, c, r, **kw):
    return dict(dict(s=s, text=text, c=c, x=1000, y=700, r=r, w=2896, h=1876, side="L"), **kw)


class BestFrameTest(unittest.TestCase):
    def test_the_best_frame_of_each_sign_is_the_biggest_ring_read_surely(self):
        found = [obs(10, "50", 90, 20), obs(11, "50", 95, 60), obs(12, "50", 99, 30), obs(30, "60", 90, 40)]
        self.assertEqual(R._best_of_groups(found, 12.0), {1, 3})

    def test_a_different_number_or_side_or_a_long_gap_is_another_sign(self):
        found = [obs(10, "50", 90, 20), obs(11, "60", 90, 20), obs(12, "50", 90, 20, side="R"), obs(40, "50", 90, 20)]
        self.assertEqual(R._best_of_groups(found, 12.0), {0, 1, 2, 3})

    def test_school_boards_keep_all_their_frames(self):
        found = [obs(10, "", 90, 20, kind="school"), obs(11, "", 90, 20, kind="school")]
        self.assertEqual(R._best_of_groups(found, 12.0), {0, 1})


class RecordTest(unittest.TestCase):
    def test_minute_signs_carry_every_frame_and_the_best_one(self):
        with tempfile.TemporaryDirectory() as d:
            rv = R.Review(d)
            st = "2026-09-08_09-21-39"
            rec = dict(n=3, rv=R.READER, d=[obs(10, "50", 90, 20), obs(11, "50", 95, 60, f=st + "_01.jpg"), obs(12, "50", 99, 30)])
            rv._day(st[:10])[st] = rec
            g = rv.minute_signs([st])[st][0]
            self.assertEqual(g["cam"], "front")
            self.assertEqual(len(g["ob"]), 3)
            self.assertEqual(g["bi"], 1)
            self.assertEqual(g["bs"], 11)
            self.assertEqual(g["f"], st + "_01.jpg")
            self.assertEqual(g["ob"][1][:2], [11, round(1000 / 2896, 4)])
            self.assertEqual(g["ob"][1][2], round(700 / 1876, 4))             # down: a fraction of the picture's height

    def test_a_record_made_before_frame_sizes_were_kept_still_gives_a_position(self):
        with tempfile.TemporaryDirectory() as d:
            rv = R.Review(d)
            st = "2026-09-08_09-21-39"
            o = obs(10, "50", 90, 20)
            del o["h"]
            rv._day(st[:10])[st] = dict(n=1, rv=R.READER, d=[o])
            g = rv.minute_signs([st])[st][0]
            self.assertEqual(g["ob"][0][2], round(700 / 1876, 4))

    def test_pictures_are_kept_for_the_best_frame_at_full_size(self):
        self.assertGreaterEqual(R.OPTS["crop_px"], 640)


if __name__ == "__main__":
    unittest.main()
