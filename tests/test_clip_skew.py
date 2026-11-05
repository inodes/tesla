#!/usr/bin/env python3
"""test_clip_skew.py - a clip is placed by its OWN recording time when its file name is out from it (lib/clip_clock.py).

The case from Parked, Sun 11 Oct 2026, 13:29 - 14:40: the clip named 14:30:34 was recorded from 14:29:50, the moment the clip before it ended (the car's clock was 44 s ahead when it
named the file). By its name there was a 44 s hole, drawn as a black "No video" minute; by its own time the recording is continuous. The time zone (the car crossed the Queensland / New South
Wales border that day: +10 h, then +11 h) is not a skew: it is a multiple of 15 minutes.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_clip_skew.py
"""
import calendar
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402


def naive(stamp):
    return calendar.timegm(__import__("datetime").datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").timetuple())


class SkewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        os.makedirs(os.path.join(self.d, "teslacam_provenance"))
        t = naive("2026-10-11_12-00-00")
        with open(os.path.join(self.d, "clip_clock.json"), "w") as f:
            json.dump(dict(v=1, runs=[[t - 36000 * 0 - 1, naive("2026-10-11_17-00-00"), 36000.0], [naive("2026-10-11_17-00-01"), naive("2026-10-11_23-59-59"), 39600.0]]), f)

    def tearDown(self):
        self.tmp.cleanup()

    def prov(self, clips):
        """clips: {stamp: (offset the car's clock was ahead, extra seconds the NAME runs ahead of the recording)}"""
        rec = {}
        for st, (off, extra) in clips.items():
            ns = naive(st)
            rec["recent||%s-front" % st] = [dict(created=ns - off - extra, modified=ns - off - extra + 60, name_stamp=ns, offset=off, skew_s=off + extra)]
        f = os.path.join(self.d, "teslacam_provenance", "2026-10-11.json")
        with open(f, "w") as fh:
            json.dump(rec, fh)
        self.tick = getattr(self, "tick", 0) + 1                                  # every write gets a time of its own: a file system with a coarse clock must not make two writes look the same
        os.utime(f, ns=(10 ** 9 * 1_700_000_000, 10 ** 9 * (1_700_000_000 + self.tick)))

    def test_the_clip_named_44_seconds_late_is_placed_by_its_own_time_and_follows_the_one_before(self):
        a, b, c = "2026-10-11_14-28-50", "2026-10-11_14-30-34", "2026-10-11_14-30-51"
        self.prov({a: (36000, 0), b: (36000, 44), c: (36000, 0)})
        CC.configure(self.d)
        self.assertEqual(CC.utc(a), naive(a) - 36000)
        self.assertEqual(CC.utc(b), naive(b) - 36000 - 44)
        self.assertEqual(CC.utc(b), CC.utc(a) + 60)                       # no hole: it begins where the clip before ended
        self.assertEqual(CC.utc(c), CC.utc(b) + 61)                       # and the next one begins where it ended
        self.assertTrue(CC.known(b))

    def test_a_name_out_by_less_than_three_seconds_and_the_time_zone_are_not_skew(self):
        a, b = "2026-10-11_14-28-50", "2026-10-11_18-30-34"
        self.prov({a: (36000, 2), b: (39600, 0)})                         # 2 s: rounding; the second clip is after the border: +11 h, the learned run
        CC.configure(self.d)
        self.assertEqual(CC.utc(a), naive(a) - 36000)
        self.assertEqual(CC.utc(b), naive(b) - 39600)

    def test_a_skewed_clip_after_the_border_is_placed_by_its_own_time_too(self):
        b = "2026-10-11_18-30-34"
        self.prov({b: (39600, 20)})
        CC.configure(self.d)
        self.assertEqual(CC.utc(b), naive(b) - 39600 - 20)

    def test_the_version_changes_when_the_recording_times_do(self):
        b = "2026-10-11_14-30-34"
        self.prov({b: (36000, 0)})
        CC.configure(self.d)
        v0 = CC.version()
        self.prov({b: (36000, 44)})
        CC._skews_refresh(force=True)
        self.assertGreater(CC.version(), v0)
        self.assertEqual(CC.utc(b), naive(b) - 36044)


if __name__ == "__main__":
    unittest.main()
