#!/usr/bin/env python3
"""test_short_clip_kept.py - a short clip the car closed early is kept when it fills the hole between two Recent minutes.

The case from Parked, Sun 11 Oct 2026, 13:29 - 14:40: the Sentry clip named 13:38:35 recorded only 9 s (13:38:35 - 13:38:44) and the Recent minute named 13:38:44 starts where it ends.
By name it is 9 s from a Recent minute, so it was taken for a repeat of it and dropped, leaving a 10 s black "No video" block at 13:38:34. By its own length it overlaps nothing.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_short_clip_kept.py
"""
import calendar
import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import teslacam_timeline as TL  # noqa: E402


def naive(stamp):
    return calendar.timegm(datetime.datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").timetuple())


class ShortClipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        os.makedirs(os.path.join(self.d, "teslacam_provenance"))
        with open(os.path.join(self.d, "clip_clock.json"), "w") as f:
            json.dump(dict(v=1, runs=[[naive("2026-10-11_00-00-00") - 36000, naive("2026-10-11_23-59-59"), 36000.0]]), f)
        self.cams = ["front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar"]

    def tearDown(self):
        self.tmp.cleanup()

    def prov(self, clips):
        rec = {}
        for st, dur in clips.items():
            c = naive(st) - 36000
            rec["x||%s-front" % st] = [dict(created=c, modified=c + dur, name_stamp=naive(st), offset=36000, skew_s=36000)]
        with open(os.path.join(self.d, "teslacam_provenance", "2026-10-11.json"), "w") as f:
            json.dump(rec, f)
        CC.configure(self.d)
        CC._skews_refresh(force=True)

    def test_the_nine_second_clip_between_two_recent_minutes_is_kept(self):
        a, s, b = "2026-10-11_13-37-34", "2026-10-11_13-38-35", "2026-10-11_13-38-44"
        self.prov({a: 60, s: 9, b: 61})
        recent = {a: self.cams, b: self.cams}
        saved = [dict(id="ev", clips={s: self.cams})]
        got = [m["stamp"] for m in TL.minutes(recent, saved)]
        self.assertEqual(got, [a, s, b])

    def test_a_whole_minute_copy_of_a_recent_minute_is_still_a_repeat(self):
        a, s = "2026-10-11_13-37-34", "2026-10-11_13-37-40"
        self.prov({a: 60, s: 60})
        got = [m["stamp"] for m in TL.minutes({a: self.cams}, [dict(id="ev", clips={s: self.cams})])]
        self.assertEqual(got, [a])

    def test_a_short_clip_inside_a_recent_minute_is_still_a_repeat(self):
        a, s = "2026-10-11_13-37-34", "2026-10-11_13-37-50"
        self.prov({a: 60, s: 9})
        got = [m["stamp"] for m in TL.minutes({a: self.cams}, [dict(id="ev", clips={s: self.cams})])]
        self.assertEqual(got, [a])

    def test_a_clip_of_unknown_length_is_judged_by_its_name_as_before(self):
        a, s, b = "2026-10-11_13-37-34", "2026-10-11_13-38-35", "2026-10-11_13-38-44"
        self.prov({a: 60, b: 61})
        got = [m["stamp"] for m in TL.minutes({a: self.cams, b: self.cams}, [dict(id="ev", clips={s: self.cams})])]
        self.assertEqual(got, [a, b])


if __name__ == "__main__":
    unittest.main()
