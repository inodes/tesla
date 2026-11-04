#!/usr/bin/env python3
"""test_minute_kind.py - what a Recent minute says about the car, from its own clip (lib/dashcam_series.telemetry_kind, lib/minute_kind.py), and how the Recent recording is cut into drives and
parks from that (lib/run_cuts.py): the clips first; a pause in Park between two minutes in gear is part of the drive; Tessie only aligns the edge of a drive the clips found.

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
PARKED = sei_nal(reading(speed_ms=0.0, gear=0))                   # telemetry, Park, 0 km/h
MOVING = sei_nal(reading(speed_ms=10.0, gear=1))                  # telemetry, Drive, 36 km/h


class TelemetryKindTest(TempDirTest):
    def kind(self, nals, **kw):
        return DS.telemetry_kind(self.write("a.mp4", clip(nals)), **kw)

    def test_moving_is_go_and_in_gear_but_standing_is_not(self):
        self.assertEqual(self.kind([MOVING, SLICE]), "go")
        self.assertEqual(self.kind([sei_nal(reading(speed_ms=0.0, gear=1))]), "pk")            # a gear touched, standing (4 Nov 08:52: Drive for 8 s at 0 km/h)
        self.assertEqual(self.kind([sei_nal(reading(speed_ms=0.3, gear=1))]), "pk")            # creeping under 3 km/h
        self.assertEqual(self.kind([sei_nal(reading(speed_ms=5.0, gear=0))]), "go")            # moving in Park (rolling)

    def test_telemetry_with_the_car_in_park_at_zero_is_pk(self):
        self.assertEqual(self.kind([PARKED, SLICE, PARKED]), "pk")
        self.assertEqual(self.kind([sei_nal(reading(speed_ms=0.03, gear=0))]), "pk")           # jitter of 0.1 km/h

    def test_no_telemetry_is_empty(self):
        self.assertEqual(self.kind([SLICE] * 200), "")

    def test_a_drive_that_began_part_way_through_the_minute_is_go(self):
        nals = [PARKED] * 20 + [SLICE] * 200 + [MOVING] + [SLICE] * 5                     # parked first, driving after: the file is not cut
        self.assertEqual(self.kind(nals), "go")
        self.assertEqual(self.kind(nals, max_nals=60), "pk")                                # looking at the start only would miss it

    def test_a_drive_that_ended_part_way_is_go(self):
        self.assertEqual(self.kind([MOVING] + [PARKED] * 20 + [SLICE] * 200), "go")

    def test_not_a_movie_is_no_answer(self):
        self.assertIsNone(DS.telemetry_kind(self.write("a.mp4", b"not a movie")))
        self.assertIsNone(DS.telemetry_kind(os.path.join(self.tmp, "missing.mp4")))

    def test_has_telemetry_is_true_for_parked_telemetry_too(self):
        self.assertIs(DS.has_telemetry(self.write("a.mp4", clip([PARKED]))), True)
        self.assertIs(DS.has_telemetry(self.write("b.mp4", clip([SLICE]))), False)


class KindsTest(TempDirTest):
    def test_each_minute_is_looked_at_once_and_kept(self):
        d, p, n = (self.write("drive.mp4", clip([MOVING])), self.write("park.mp4", clip([PARKED])), self.write("none.mp4", clip([SLICE] * 30)))
        files = {"2026-11-04_09-00-00": d, "2026-11-04_09-01-00": p, "2026-11-04_09-02-00": n, "2026-11-04_09-03-00": None}
        looked = []
        k = MK.Kinds(os.path.join(self.tmp, "k.json"))
        count = MK.probe(k, list(files), lambda st: (looked.append(st), files[st])[1])
        self.assertEqual(count, 3)                                              # the one with no file is left for later
        self.assertEqual([k.get(s) for s in files], ["go", "pk", "", None])
        looked.clear()
        k2 = MK.Kinds(os.path.join(self.tmp, "k.json"))                         # kept across runs: not looked at again
        MK.probe(k2, list(files), lambda st: (looked.append(st), files[st])[1])
        self.assertEqual(looked, ["2026-11-04_09-03-00"])
        self.assertEqual(k2.all(), {"2026-11-04_09-00-00": "go", "2026-11-04_09-01-00": "pk", "2026-11-04_09-02-00": ""})

    def test_all_unread_minutes_are_read_in_the_background_and_kept(self):
        import time
        d, p = self.write("drive.mp4", clip([MOVING])), self.write("park.mp4", clip([PARKED]))
        files = {"2026-11-04_09-00-00": d, "2026-11-04_09-01-00": p, "2026-11-04_09-02-00": None}
        k = MK.Kinds(os.path.join(self.tmp, "k.json"))
        self.assertTrue(MK.probe_bg(k, list(files), files.get))
        for _ in range(100):
            if not MK._BG["on"]:
                break
            time.sleep(0.05)
        self.assertEqual([k.get(s) for s in files], ["go", "pk", None])
        self.assertEqual(MK.Kinds(os.path.join(self.tmp, "k.json")).all(), {"2026-11-04_09-00-00": "go", "2026-11-04_09-01-00": "pk"})        # kept: the file changing is what rebuilds the lists
        self.assertFalse(MK.probe_bg(k, list(files), files.get))                # nothing left that can be read


def stamps(first_min, n, hour=9):
    return ["2026-11-04_%02d-%02d-07" % (hour + (first_min + k) // 60, (first_min + k) % 60) for k in range(n)]


def classes(text, sts):
    """'DDpp..' -> {stamp: class}: D in gear or moving, p Park with telemetry, . no telemetry."""
    return {st: {"D": "go", "p": "pk", ".": ""}[c] for st, c in zip(sts, text)}


class SectionsTest(unittest.TestCase):
    def runs(self, text, moves=None, upto=None):
        sts = stamps(0, len(text))
        got = RC.minute_sections(sts, classes(text, sts), moves, upto or CC.utc(sts[-1]) + 3600)
        out, last = [], None
        for i, st in enumerate(sts):
            if got[st] != last:
                out.append((i, got[st]))
                last = got[st]
        return out

    def trip(self, n, a, b, lead=15, tail=40):
        sts = stamps(0, n)
        return [dict(t0=CC.utc(sts[a]) + lead, t1=CC.utc(sts[b]) + tail)]

    def test_in_gear_is_a_drive_and_telemetry_in_park_with_no_telemetry_is_one_park(self):
        self.assertEqual(self.runs("....DDDDDDpppp...."), [(0, "park"), (4, "drive"), (10, "park")])      # the amber and grey clips after a drive are one continuous park
        self.assertEqual(self.runs("pppp...DDDD"), [(0, "park"), (7, "drive")])                              # and before one

    def test_a_pause_in_park_between_two_stretches_in_gear_is_one_drive(self):
        self.assertEqual(self.runs("DDpDDDD"), [(0, "drive")])                                               # a door to open, a child to put in
        self.assertEqual(self.runs("..DpppDD.."), [(0, "park"), (2, "drive"), (8, "park")])

    def test_a_long_stop_or_one_with_no_telemetry_is_a_park_between_two_drives(self):
        self.assertEqual(self.runs("DDDppppppDDD"), [(0, "drive"), (3, "park"), (9, "drive")])               # six minutes: not a pause
        self.assertEqual(self.runs("DDD...DDD"), [(0, "drive"), (3, "park"), (6, "drive")])                  # the car off: a park

    def test_a_gap_between_clips_starts_a_new_run(self):
        sts = stamps(0, 3) + stamps(10, 3)
        got = RC.minute_sections(sts, {st: "go" for st in sts})
        self.assertEqual(set(got.values()), {"drive"})

    def test_tessie_aligns_the_start_and_end_of_a_drive_the_clips_found(self):
        self.assertEqual(self.runs("..DDDDDDDDD...", self.trip(14, 3, 9)), [(0, "park"), (3, "drive"), (10, "park")])          # Tessie starts a block later and ends a block earlier: it wins
        self.assertEqual(self.runs("..DDDDDDDD....", self.trip(14, 1, 10)), [(0, "park"), (1, "drive"), (11, "park")])         # and one block wider

    def test_tessie_moves_an_edge_by_a_few_blocks_at_most(self):
        text = "D" * 24
        self.assertEqual(self.runs(text, self.trip(24, 8, 20)), [(0, "drive")])                                # eight blocks off: not the same drive, the clips stand
        self.assertEqual(self.runs(text, self.trip(24, 2, 21)), [(0, "park"), (2, "drive"), (22, "park")])    # two blocks: aligned

    def test_the_pause_in_the_middle_is_kept_when_tessie_has_the_drive(self):
        self.assertEqual(self.runs("..DDpDDDD...", self.trip(12, 2, 8)), [(0, "park"), (2, "drive"), (9, "park")])

    def test_a_stop_between_two_drives_of_tessie_is_a_park_not_a_pause(self):                       # 4 Nov: Home -> School -> Preschool -> Soccer, the car awake at each stop
        two = self.trip(11, 0, 3) + self.trip(11, 7, 10, lead=5)
        self.assertEqual(self.runs("DDDDpppDDDD", two), [(0, "drive"), (4, "park"), (7, "drive")])
        self.assertEqual(self.runs("DDDDpppDDDD", self.trip(11, 0, 10)), [(0, "drive")])            # one trip of Tessie's across it: a pause, as the clips said
        self.assertEqual(self.runs("DDDDpppDDDD", two, CC.utc(stamps(0, 11)[5])), [(0, "drive")])     # Tessie not complete over the stop: the clips' pause rule stands

    def test_a_stop_in_park_between_two_stretches_of_driving_is_two_drives(self):                # 4 Nov: Home -> School (Park for a moment) -> Preschool
        sts = stamps(0, 8)
        kinds = classes("DDDDDDDD", sts)
        marks = {st: (0, 0, None) for st in sts}
        marks[sts[3]], marks[sts[4]] = (0, 5, None), (4, 0, None)                                    # Park at the end of one clip and the start of the next
        got = RC.minute_sections(sts, kinds, None, 0, marks)
        self.assertEqual([got[st] for st in sts], ["drive"] * 4 + ["drive#1"] * 4)
        marks[sts[3]], marks[sts[4]] = (0, 0, None), (0, 0, None)                                    # no Park there: one drive
        self.assertEqual(set(RC.minute_sections(sts, kinds, None, 0, marks).values()), {"drive"})
        marks[sts[5]] = (0, 0, 0.8)                                                                  # a stop inside a clip goes to the nearer join
        got = RC.minute_sections(sts, kinds, None, 0, marks)
        self.assertEqual([got[st] for st in sts], ["drive"] * 6 + ["drive#1"] * 2)

    def test_stop_marks_read_the_gear(self):
        self.assertEqual(RC.stop_marks([1] * 10), (0, 0, None))
        self.assertEqual(RC.stop_marks([1] * 4 + [0] * 3 + [1] * 3), (0, 0, 0.55))
        self.assertEqual(RC.stop_marks([0] * 4 + [1] * 6), (4, 0, None))
        self.assertEqual(RC.stop_marks([1] * 6 + [0] * 4), (0, 4, None))
        self.assertEqual(RC.stop_marks([0] * 10), (0, 0, None))                                       # parked all minute: no stop between driving

    def test_tessie_does_not_make_a_drive_the_clips_did_not_find(self):
        self.assertEqual(self.runs("....pppp....", self.trip(12, 2, 8)), [(0, "park")])

    def test_where_tessie_is_not_complete_yet_the_clips_stand(self):
        sts = stamps(0, 14)
        self.assertEqual(self.runs("..DDDDDDDDD...", self.trip(14, 3, 9), CC.utc(sts[5])), [(0, "park"), (2, "drive"), (11, "park")])

    def test_a_drive_is_not_pushed_into_the_next_one(self):
        self.assertEqual(self.runs("DDDD...DDDDDDDDD", self.trip(16, 1, 12)), [(0, "drive"), (4, "park"), (7, "drive")])


class CutsFromClipsTest(unittest.TestCase):
    def setUp(self):
        RC.set_file(os.path.join(tempfile.mkdtemp(), "run_cuts.json"))
        self.sts = stamps(0, 40)
        self.rec = [dict(id=self.sts[0], kind="recording", t0=CC.utc(self.sts[0]), t1=CC.utc(self.sts[-1]) + 60)]       # telemetry not read yet

    def kinds(self, text):
        return classes(text.ljust(40, "."), self.sts)

    def test_a_run_is_cut_where_the_clips_change(self):
        c = RC.compute(self.sts, self.rec, kinds=self.kinds(".....DDDDDDDDDD.......DDDDD"))
        self.assertEqual([(k, v[0], v[3]) for k, v in sorted(c.items())],
                         [(self.sts[0], "park", False), (self.sts[5], "drive", True), (self.sts[15], "park", True), (self.sts[22], "drive", True), (self.sts[27], "park", True)])

    def test_a_pause_in_park_does_not_cut_a_drive(self):
        c = RC.compute(self.sts, self.rec, kinds=self.kinds("..DDDpDDDD"))
        self.assertEqual([(k, v[0]) for k, v in sorted(c.items())], [(self.sts[0], "park"), (self.sts[2], "drive"), (self.sts[10], "park")])

    def test_the_other_record_does_not_cut_and_does_not_make_a_drive(self):
        moved = [dict(t0=CC.utc(self.sts[5]), t1=CC.utc(self.sts[14]) + 60, p0=[-30.0, 153.0], p1=[-30.1, 153.1])]
        c = RC.compute(self.sts, self.rec, moves=moved, moves_upto=CC.utc(self.sts[-1]) + 3600)
        self.assertEqual(len(c), 1)                                              # no clips looked at: no cuts, whatever Tessie says
        c = RC.compute(self.sts, self.rec, moves=moved, moves_upto=CC.utc(self.sts[-1]) + 3600, kinds=self.kinds("pppppppp"))
        self.assertEqual({v[0] for v in c.values()}, {"park"})                  # the car sat awake in Park: no drive

    def test_a_park_is_where_the_drive_before_it_ended(self):
        moved = [dict(t0=CC.utc(self.sts[2]) + 5, t1=CC.utc(self.sts[9]) + 30, p0=[-30.0, 153.0], p1=[-30.1, 153.1])]
        c = RC.compute(self.sts, self.rec, moves=moved, moves_upto=CC.utc(self.sts[-1]) + 3600, kinds=self.kinds("..DDDDDDDD......"))
        self.assertEqual(c[self.sts[10]][:3], ["park", -30.1, 153.1])


class ConfirmTest(unittest.TestCase):
    sts = stamps(0, 20)

    def moves(self, a, b):
        return [dict(t0=CC.utc(self.sts[a]) + 15, t1=CC.utc(self.sts[b]) + 40)]

    def kinds(self, text):
        return classes(text.ljust(20, "."), self.sts)

    def test_the_other_record_confirms_what_the_clips_say(self):
        self.assertEqual(RC.disagreements(self.kinds(".....DDDDDDDDDD"), self.moves(5, 14), CC.utc(self.sts[-1]) + 3600), [])

    def test_the_car_awake_in_gear_a_little_before_and_after_the_trip_is_confirmed(self):
        self.assertEqual(RC.disagreements(self.kinds("...DDDDDDDDDDDDDD"), self.moves(5, 14), CC.utc(self.sts[-1]) + 3600), [])

    def test_it_says_where_they_differ(self):
        d = RC.disagreements(self.kinds("...................D"), self.moves(5, 14), CC.utc(self.sts[-1]) + 3600)
        self.assertIn((self.sts[19], "clip drive, no drive of Tessie's"), d)                # telemetry in gear with no trip of Tessie's near
        k = RC.disagreements(self.kinds(""), self.moves(5, 14), CC.utc(self.sts[-1]) + 3600)
        self.assertIn((self.sts[9], "clip parked, Tessie drove"), k)

    def test_minutes_tessie_is_not_complete_for_are_not_judged(self):
        self.assertEqual(RC.disagreements(self.kinds(""), self.moves(5, 14), CC.utc(self.sts[3])), [])


if __name__ == "__main__":
    unittest.main()
