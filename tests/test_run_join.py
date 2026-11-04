#!/usr/bin/env python3
"""A hole in the Recent clips that Saved clips (a drive) or Sentry clips (a park) fill is one recording, not two runs."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_teslacam as AT  # noqa: E402


def stamp(minute, sec=7, day="2026-10-11", hour=14):
    return "%s_%02d-%02d-%02d" % (day, hour + minute // 60, minute % 60, sec)


def run(a, b):
    sts = [stamp(m) for m in range(a, b + 1)]
    return dict(stamps=sts, t1=AT._secs(sts[-1]))


def fill(a, b):
    return sorted(AT._secs(stamp(m, 9)) for m in range(a, b + 1))        # Saved / Sentry copies are named a little differently from Recent's


DRIVE = ("drive", -27.97, 153.42)
PARK = ("park", -29.15, 153.29)


class JoinTest(unittest.TestCase):
    def test_drive_hole_filled_by_saved_is_one_run(self):
        with mock.patch.object(AT.RC, "section", lambda st: DRIVE):
            out = AT.join_runs([run(0, 57), run(69, 110)], fill(58, 68))
        self.assertEqual(len(out), 1)
        self.assertEqual((len(out[0]["stamps"]), out[0]["t1"]), (58 + 42, AT._secs(stamp(110))))

    def test_hole_nothing_fills_stays_two_runs(self):
        with mock.patch.object(AT.RC, "section", lambda st: DRIVE):
            self.assertEqual(len(AT.join_runs([run(0, 57), run(69, 110)], fill(58, 60))), 2)           # only three of the eleven minutes
            self.assertEqual(len(AT.join_runs([run(0, 57), run(69, 110)], [])), 2)

    def test_two_sections_are_never_joined(self):
        sec = {stamp(m): DRIVE for m in range(0, 58)}
        sec.update({stamp(m): PARK for m in range(69, 111)})
        with mock.patch.object(AT.RC, "section", lambda st: sec.get(st)):
            self.assertEqual(len(AT.join_runs([run(0, 57), run(69, 110)], fill(58, 68))), 2)

    def test_park_hole_filled_by_sentry_is_one_run(self):
        with mock.patch.object(AT.RC, "section", lambda st: PARK):
            out = AT.join_runs([run(0, 20), run(31, 40), run(41, 50)], fill(21, 30))
        self.assertEqual([len(r["stamps"]) for r in out], [21 + 10 + 10])                              # the second and third follow on by themselves

    def test_parks_at_one_place_join_whatever_their_positions(self):                      # 4 Nov: Ren's Soccer, Sentry events in the middle of one continuous park
        sec = {stamp(m): ("park", -33.8000, 151.1000) for m in range(0, 21)}
        sec.update({stamp(m): ("park", -33.8003, 151.1002) for m in range(31, 41)})          # carried / estimated, a few metres off
        with mock.patch.object(AT.RC, "section", lambda st: sec.get(st)):
            self.assertEqual(len(AT.join_runs([run(0, 20), run(31, 40)], fill(21, 30))), 1)
        sec.update({stamp(m): ("park", None, None) for m in range(31, 41)})                  # no position of its own
        with mock.patch.object(AT.RC, "section", lambda st: sec.get(st)):
            self.assertEqual(len(AT.join_runs([run(0, 20), run(31, 40)], fill(21, 30))), 1)

    def test_parks_at_two_places_are_not_joined(self):
        sec = {stamp(m): ("park", -33.8000, 151.1000) for m in range(0, 21)}
        sec.update({stamp(m): ("park", -33.9000, 151.2000) for m in range(31, 41)})          # 14 km away: a drive between them that was saved
        with mock.patch.object(AT.RC, "section", lambda st: sec.get(st)):
            self.assertEqual(len(AT.join_runs([run(0, 20), run(31, 40)], fill(21, 30))), 2)

    def test_unknown_section_is_never_joined(self):
        with mock.patch.object(AT.RC, "section", lambda st: None):
            self.assertEqual(len(AT.join_runs([run(0, 5), run(8, 12)], fill(6, 7))), 2)

    def test_a_short_hole_of_part_of_a_minute_joins(self):
        a, b = run(0, 10), dict(stamps=[stamp(11, 23)], t1=AT._secs(stamp(11, 23)))              # 16 s after the minute ends
        with mock.patch.object(AT.RC, "section", lambda st: DRIVE):
            self.assertEqual(len(AT.join_runs([a, b], [])), 1)


if __name__ == "__main__":
    unittest.main()
