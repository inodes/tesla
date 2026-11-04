#!/usr/bin/env python3
"""test_refine_edges.py - a dashcam-only drive ends, and the park after it begins, at the second Park was engaged (lib/run_cuts.refine_edges), the minute staying in both.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_refine_edges.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import run_cuts as RC  # noqa: E402

T = 1000000


class RefineEdgesTest(unittest.TestCase):
    def test_drive_ends_when_park_was_engaged_and_the_park_begins_there(self):
        items = [dict(kind="drive", t0=T, t1=T + 600), dict(kind="stopped", t0=T + 600, t1=T + 1800)]
        out = RC.refine_edges(items, {"m9": (0, 20, None)}, {T + 540: "m9"})
        self.assertEqual([(i["kind"], i["t0"], i["t1"]) for i in out], [("drive", T, T + 580), ("stopped", T + 580, T + 1800)])
        self.assertEqual(items[0]["t1"], T + 600)                      # the input is not changed

    def test_drive_starts_when_park_was_left(self):
        items = [dict(kind="parked", t0=T, t1=T + 600), dict(kind="drive", t0=T + 600, t1=T + 1200)]
        out = RC.refine_edges(items, {"m0": (25, 0, None)}, {T + 600: "m0"})
        self.assertEqual([(i["kind"], i["t0"], i["t1"]) for i in out], [("parked", T, T + 625), ("drive", T + 625, T + 1200)])

    def test_nothing_changes_without_a_mark_for_a_gap_or_for_tessies_rows(self):
        items = [dict(kind="drive", t0=T, t1=T + 600), dict(kind="stopped", t0=T + 600, t1=T + 1800)]
        self.assertEqual(RC.refine_edges(items, {}, {T + 540: "m9"}), items)
        gap = [dict(kind="drive", t0=T, t1=T + 600), dict(kind="stopped", t0=T + 900, t1=T + 1800)]
        self.assertEqual(RC.refine_edges(gap, {"m9": (0, 20, None)}, {T + 540: "m9"}), gap)
        tess = [dict(kind="drive", t0=T, t1=T + 600, src="tessie"), dict(kind="stopped", t0=T + 600, t1=T + 1800)]
        self.assertEqual(RC.refine_edges(tess, {"m9": (0, 20, None)}, {T + 540: "m9"}), tess)

    def test_a_whole_minute_in_park_or_a_tiny_drive_is_left_alone(self):
        items = [dict(kind="drive", t0=T, t1=T + 60), dict(kind="stopped", t0=T + 60, t1=T + 600)]
        self.assertEqual(RC.refine_edges(items, {"m": (0, 50, None)}, {T: "m"}), items)

    def test_a_drive_cannot_end_in_motion_the_parking_movements_in_the_next_minute_are_in_it(self):
        items = [dict(kind="drive", t0=T, t1=T + 600), dict(kind="stopped", t0=T + 600, t1=T + 1800)]
        out = RC.refine_edges(items, {}, {T + 540: "a", T + 600: "b"}, lambda st: (14, 0) if st == "b" else None)      # the park's first minute starts with 14 s in gear
        self.assertEqual([(i["t0"], i["t1"]) for i in out], [(T, T + 614), (T + 614, T + 1800)])

    def test_pulling_away_in_the_last_minute_of_a_park_starts_the_drive_there(self):
        items = [dict(kind="stopped", t0=T, t1=T + 600), dict(kind="drive", t0=T + 600, t1=T + 1200)]
        out = RC.refine_edges(items, {}, {T + 540: "p", T + 600: "d"}, lambda st: (0, 18) if st == "p" else None)
        self.assertEqual([(i["t0"], i["t1"]) for i in out], [(T, T + 582), (T + 582, T + 1200)])

    def test_gear_edges(self):
        self.assertEqual(RC.gear_edges([1] * 14 + [0] * 46), (14, 0))
        self.assertEqual(RC.gear_edges([0] * 40 + [2] * 20), (0, 20))
        self.assertEqual(RC.gear_edges([0] * 60), (0, 0))
        self.assertEqual(RC.gear_edges([1] * 60), (0, 0))
        self.assertEqual(RC.gear_edges([]), (0, 0))


if __name__ == "__main__":
    unittest.main()
