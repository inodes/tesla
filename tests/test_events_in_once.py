#!/usr/bin/env python3
"""test_events_in_once.py - the Sentry minutes inside a window: the times of the drive's Recent minutes are worked out once for all the events (not once
per event), and the answer is what asking each event on its own gives.

    python3 -m unittest tests/test_events_in_once.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_teslacam  # noqa: E402

TL = app_teslacam.TL


class Once(unittest.TestCase):
    def setUp(self):
        self.tc = app_teslacam.TeslaCam(tempfile.mkdtemp())
        recent = {"2026-10-01_10-%02d-00" % m: ["front"] for m in range(0, 30)}
        sentry = [dict(id="2026-10-01_%02d-05-00" % h, clips={"2026-10-01_%02d-%02d-00" % (h, m): ["front", "back"] for m in range(0, 6)}) for h in (9, 10, 11, 12)]
        self.tc._sources = lambda role, root: (recent, [], sentry)
        self.real, self.n = TL.secs, [0]

        def secs(st):
            self.n[0] += 1
            return self.real(st)
        TL.secs = secs

    def tearDown(self):
        TL.secs = self.real

    def test_same_minutes_as_each_event_alone(self):
        t0, t1 = self.real("2026-10-01_08-00-00"), self.real("2026-10-01_13-00-00")
        got = self.tc._events_in("A", "/x", t0, t1)
        want, seen = [], set()
        for e in self.tc._sources("A", "/x")[2]:
            want += [m for m in self.tc._sentry_minutes("A", "/x", e["id"]) if m["stamp"] not in seen and not seen.add(m["stamp"]) and m["t"] + 60 > t0 and m["t"] < t1]
        self.assertEqual(got, want)
        self.assertEqual(sorted({m["ref"] for m in got}), ["2026-10-01_09-05-00", "2026-10-01_11-05-00", "2026-10-01_12-05-00"])     # 10:xx is held by Recent

    def test_recent_times_worked_out_once(self):
        self.tc._events_in("A", "/x", 0, 2e9)
        self.assertEqual(self.n[0], 30 + 4 * 6)                 # each Recent minute once, each event minute once
