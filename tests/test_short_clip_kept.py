#!/usr/bin/env python3
"""test_short_clip_kept.py - a short clip the car closed early is kept when it fills the hole between two Recent minutes.

The case from Parked, Sun 11 Oct 2026, 13:29 - 14:40: the Sentry clip named 13:38:35 recorded only 9 s (13:38:35 - 13:38:44) and the Recent minute named 13:38:44 starts where it ends.
By name it is 9 s from a Recent minute, so it was taken for a repeat of it and dropped, leaving a 10 s black "No video" block at 13:38:34. Clips never overlap (see docs/CLIP_TIMES.md), so nothing is dropped for starting near another clip.
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
        saved_s, saved_sk = dict(CC._S), dict(CC._SK)
        self.addCleanup(lambda: (CC._S.clear(), CC._S.update(saved_s), CC._SK.clear(), CC._SK.update(saved_sk)))          # the learned clock of this test must not reach the tests after it
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

    def test_a_clip_is_the_same_clip_only_by_its_name(self):
        a, s = "2026-10-11_13-37-34", "2026-10-11_13-37-40"
        self.prov({a: 60, s: 60})
        got = [m["stamp"] for m in TL.minutes({a: self.cams}, [dict(id="ev", clips={s: self.cams})])]
        self.assertEqual(got, [a, s])                                   # clips never overlap: one that starts near another is a different stretch of time
        got = [m["stamp"] for m in TL.minutes({a: self.cams}, [dict(id="ev", clips={a: self.cams})])]
        self.assertEqual(got, [a])                                      # the same file on two sources is one minute

    def test_a_clip_whose_length_is_not_read_yet_is_kept_too(self):
        a, s, b = "2026-10-11_13-37-34", "2026-10-11_13-38-35", "2026-10-11_13-38-44"
        self.prov({a: 60, b: 61})                                       # the length of s is not known (its provenance is not built yet)
        got = [m["stamp"] for m in TL.minutes({a: self.cams, b: self.cams}, [dict(id="ev", clips={s: self.cams})])]
        self.assertEqual(got, [a, s, b])


if __name__ == "__main__":
    unittest.main()
