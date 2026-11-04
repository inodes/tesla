#!/usr/bin/env python3
"""test_minute_kind.py - a Recent minute is a drive or a park from its own clip: any telemetry in it = drive, none = parked, each minute whole (lib/dashcam_series.has_telemetry,
lib/minute_kind.py), and the Recent recording is cut where that changes (lib/run_cuts.py), the dashcam first and no other record.

Made-up clips only (the helpers of test_dashcam_telemetry).
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_minute_kind.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import minute_kind as MK  # noqa: E402
import run_cuts as RC  # noqa: E402
from lib import dashcam_series as DS  # noqa: E402
from test_dashcam_telemetry import TempDirTest, clip, reading, sei_nal  # noqa: E402

SLICE = bytes([0x41, 0x9a, 0x24, 0x6c] + [0] * 40)               # a video slice: not telemetry


class HasTelemetryTest(TempDirTest):
    def test_a_clip_with_telemetry_is_a_drive(self):
        self.assertIs(DS.has_telemetry(self.write("a.mp4", clip([sei_nal(reading()), SLICE]))), True)

    def test_a_clip_with_none_is_parked(self):
        self.assertIs(DS.has_telemetry(self.write("a.mp4", clip([SLICE] * 200))), False)

    def test_a_drive_that_began_part_way_through_the_minute_is_a_drive(self):
        nals = [SLICE] * 200 + [sei_nal(reading())] + [SLICE] * 5                # parked for the first frames, driving after: the file is not cut
        self.assertIs(DS.has_telemetry(self.write("a.mp4", clip(nals))), True)
        self.assertIs(DS.has_telemetry(self.write("b.mp4", clip(nals)), max_nals=60), False)    # looking at the start only would miss it

    def test_a_drive_that_ended_part_way_is_a_drive(self):
        self.assertIs(DS.has_telemetry(self.write("a.mp4", clip([sei_nal(reading(speed_ms=0.0))] + [SLICE] * 200))), True)

    def test_not_a_movie_is_no_answer(self):
        self.assertIsNone(DS.has_telemetry(self.write("a.mp4", b"not a movie")))
        self.assertIsNone(DS.has_telemetry(os.path.join(self.tmp, "missing.mp4")))


class KindsTest(TempDirTest):
    def test_each_minute_is_looked_at_once_and_kept(self):
        d, p = self.write("drive.mp4", clip([sei_nal(reading())])), self.write("park.mp4", clip([SLICE] * 30))
        files = {"2026-11-04_09-00-00": d, "2026-11-04_09-01-00": p, "2026-11-04_09-02-00": None}
        looked = []
        k = MK.Kinds(os.path.join(self.tmp, "k.json"))
        n = MK.probe(k, list(files), lambda st: (looked.append(st), files[st])[1])
        self.assertEqual(n, 2)                                                  # the one with no file is left for later
        self.assertEqual([k.get(s) for s in files], [True, False, None])
        looked.clear()
        k2 = MK.Kinds(os.path.join(self.tmp, "k.json"))                         # kept across runs: not looked at again
        MK.probe(k2, list(files), lambda st: (looked.append(st), files[st])[1])
        self.assertEqual(looked, ["2026-11-04_09-02-00"])
        self.assertEqual(k2.all(), {"2026-11-04_09-00-00": True, "2026-11-04_09-01-00": False})


def stamps(first_min, n, hour=9):
    return ["2026-11-04_%02d-%02d-07" % (hour + (first_min + k) // 60, (first_min + k) % 60) for k in range(n)]


class CutsFromClipsTest(unittest.TestCase):
    def setUp(self):
        RC.set_file(os.path.join(tempfile.mkdtemp(), "run_cuts.json"))
        self.sts = stamps(0, 40)
        self.rec = [dict(id=self.sts[0], kind="recording", t0=CC.utc(self.sts[0]), t1=CC.utc(self.sts[-1]) + 60)]       # telemetry not read yet

    def kinds(self, drives):
        return {st: any(a <= i <= b for a, b in drives) for i, st in enumerate(self.sts)}

    def test_a_run_is_cut_where_the_clips_change(self):
        c = RC.compute(self.sts, self.rec, kinds=self.kinds([(5, 14), (22, 26)]))
        self.assertEqual([(k, v[0], v[3]) for k, v in sorted(c.items())],
                         [(self.sts[0], "park", False), (self.sts[5], "drive", True), (self.sts[15], "park", True), (self.sts[22], "drive", True), (self.sts[27], "park", True)])

    def test_a_park_stays_a_park_and_a_drive_a_drive_until_it_changes(self):
        c = RC.compute(self.sts, self.rec, kinds=self.kinds([(10, 30)]))
        self.assertEqual(sorted(c), [self.sts[0], self.sts[10], self.sts[31]])

    def test_the_other_record_does_not_cut(self):
        moved = [dict(t0=CC.utc(self.sts[5]), t1=CC.utc(self.sts[14]) + 60, p0=[-30.0, 153.0], p1=[-30.1, 153.1])]
        c = RC.compute(self.sts, self.rec, moves=moved, moves_upto=CC.utc(self.sts[-1]) + 3600)
        self.assertEqual(len(c), 1)                                              # no clips looked at: no cuts, whatever Tessie says

    def test_a_drive_read_in_part_and_the_rest_told_by_its_clips_is_one_drive(self):
        items = [dict(id=self.sts[5], kind="drive", t0=CC.utc(self.sts[5]), t1=CC.utc(self.sts[9]) + 60, p0=[-30.0, 153.0], p1=[-30.1, 153.1]),
                 dict(id=self.sts[10], kind="recording", t0=CC.utc(self.sts[10]), t1=CC.utc(self.sts[39]) + 60)]
        k = {st: (5 <= i <= 14) for i, st in enumerate(self.sts) if i >= 10}
        c = RC.compute(self.sts[5:], items, kinds=k)
        self.assertEqual([(s, v[0]) for s, v in sorted(c.items())], [(self.sts[5], "drive"), (self.sts[15], "park")])


class ConfirmTest(unittest.TestCase):
    sts = stamps(0, 20)

    def moves(self, a, b):
        return [dict(t0=CC.utc(self.sts[a]) + 15, t1=CC.utc(self.sts[b]) + 40)]

    def test_the_other_record_confirms_what_the_clips_say(self):
        k = {st: (5 <= i <= 14) for i, st in enumerate(self.sts)}
        self.assertEqual(RC.disagreements(k, self.moves(5, 14), CC.utc(self.sts[-1]) + 3600), [])

    def test_the_car_wakes_in_gear_a_little_before_and_after_the_trip(self):
        k = {st: (3 <= i <= 16) for i, st in enumerate(self.sts)}                      # two minutes of telemetry either side of the trip
        self.assertEqual(RC.disagreements(k, self.moves(5, 14), CC.utc(self.sts[-1]) + 3600), [])

    def test_it_says_where_they_differ(self):
        k = {st: False for st in self.sts}
        k[self.sts[18]] = True                                                         # telemetry with no trip of Tessie's near
        d = RC.disagreements(k, self.moves(5, 14), CC.utc(self.sts[-1]) + 3600)
        self.assertIn((self.sts[18], "clip drive, no drive of Tessie's"), d)
        self.assertIn((self.sts[9], "clip parked, Tessie drove"), d)

    def test_minutes_tessie_is_not_complete_for_are_not_judged(self):
        k = {st: False for st in self.sts}
        self.assertEqual(RC.disagreements(k, self.moves(5, 14), CC.utc(self.sts[3])), [])


if __name__ == "__main__":
    unittest.main()
