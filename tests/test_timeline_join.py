"""test_timeline_join.py - a few skipped seconds between two clips of a moving car do not split one drive in two (the 363 km drive of 12 Oct 2026 was shown as two).

    python3 -m unittest tests/test_timeline_join.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import teslacam_timeline as TL  # noqa: E402


class Store:
    def __init__(self, sums):
        self.s = sums

    def summary(self, stamp):
        return self.s.get(stamp)

    def series(self, stamp):
        return None


class GearStore(Store):
    """A store whose minutes also carry the car's gear per second (0 Park, 1 Drive), as the real telemetry does."""
    def __init__(self, sums, gears):
        Store.__init__(self, sums)
        self.g = gears

    def series(self, stamp):
        g = self.g.get(stamp)
        top = (self.s.get(stamp) or {}).get("vmax", 0)
        return dict(gear=g, v=[top if x else 0 for x in g], lat=[-32.0] * len(g), lon=[151.0] * len(g), ap=[0] * len(g)) if g else None


def minute(first, last, v=100, lat0=-32.0, lat1=-32.01):
    """A read minute in gear from `first` to `last` seconds as 60 samples: gear changes once, halfway."""
    sm = dict(km=1.6, mov=60 if v else 0, fsd=0, aut=0, vmax=v, p0=[lat0, 151.0], p1=[lat1, 151.0])
    return sm, [first] * 30 + [last] * 30


def move(lat0, lat1, v=100):
    return dict(km=1.6, mov=60, fsd=0, aut=0, vmax=v, p0=[lat0, 151.0], p1=[lat1, 151.0])


def run(stamps, sums):
    items = TL.build({s: ["front"] for s in stamps}, [], [], Store(sums))
    return [i for i in items if i["kind"] == "drive"]


class Join(unittest.TestCase):
    A, B, C = "2026-10-12_13-08-00", "2026-10-12_13-09-00", "2026-10-12_13-10-16"     # C starts 76 s after B (a 16 s hole)

    def test_short_hole_between_moving_minutes_is_one_drive(self):
        sums = {self.A: move(-32.00, -32.01), self.B: move(-32.01, -32.02), self.C: move(-32.0225, -32.03)}
        self.assertEqual(len(run([self.A, self.B, self.C], sums)), 1)

    def test_far_apart_positions_are_two_drives(self):
        sums = {self.A: move(-32.00, -32.01), self.B: move(-32.01, -32.02), self.C: move(-32.30, -32.31)}      # 30 km away after a 16 s hole
        self.assertEqual(len(run([self.A, self.B, self.C], sums)), 2)

    def test_long_hole_is_two_drives(self):
        late = "2026-10-12_13-16-00"                                                        # 7 minutes on
        sums = {self.A: move(-32.00, -32.01), self.B: move(-32.01, -32.02), late: move(-32.02, -32.03)}
        self.assertEqual(len(run([self.A, self.B, late], sums)), 2)

    def test_a_stopped_side_is_not_joined(self):
        sums = {self.A: move(-32.00, -32.01), self.B: move(-32.01, -32.02), self.C: dict(km=0, mov=0, fsd=0, aut=0, vmax=0, p0=[-32.02, 151.0], p1=[-32.02, 151.0])}
        self.assertEqual(len(run([self.A, self.B, self.C], sums)), 1)       # the moving part only; the stopped minute is its own item

    def test_unread_minutes_are_never_joined_by_guess(self):
        sums = {self.A: move(-32.00, -32.01), self.B: move(-32.01, -32.02)}                  # C not read yet
        items = TL.build({s: ["front"] for s in (self.A, self.B, self.C)}, [], [], Store(sums))
        self.assertEqual(sorted(i["kind"] for i in items if i["kind"] != "gap"), ["drive", "recording"])


def gear_run(minutes):
    """minutes = [(stamp, (summary, gears))]: the drive / stopped items the timeline makes."""
    st = GearStore({k: v[0] for k, v in minutes}, {k: v[1] for k, v in minutes})
    items = TL.build({k: ["front"] for k, _ in minutes}, [], [], st)
    return sorted(((i["kind"], i["start"][11:16]) for i in items if i["kind"] in ("drive", "stopped")), key=lambda x: x[1])


class Park(unittest.TestCase):
    A, B, C = "2026-10-12_13-08-00", "2026-10-12_13-09-00", "2026-10-12_13-10-16"      # a 16 s hole before C

    def test_hole_while_in_drive_is_one_drive_wherever_the_car_is(self):
        far = minute(1, 1, lat0=-32.40, lat1=-32.41)                                   # the position is not the test: the gear is
        r = gear_run([(self.A, minute(1, 1)), (self.B, minute(1, 1, lat0=-32.01, lat1=-32.02)), (self.C, far)])
        self.assertEqual([k for k, _ in r], ["drive"])

    def test_going_into_park_ends_the_drive_even_with_no_hole(self):
        r = gear_run([(self.A, minute(1, 1)), (self.B, minute(1, 0)), ("2026-10-12_13-10-00", minute(0, 0, v=0))])
        self.assertEqual([k for k, _ in r], ["drive", "stopped"])                      # the minute that ends in Park is the last of the drive; the Park minute after it is a stop

    def test_hole_after_park_is_two_drives(self):
        r = gear_run([(self.A, minute(1, 1)), (self.B, minute(1, 0)), (self.C, minute(1, 1))])
        self.assertEqual([k for k, _ in r], ["drive", "drive"])

    def test_stop_at_a_light_in_drive_is_still_the_drive(self):
        light = minute(1, 1, v=0)                                                       # 0 km/h for the whole minute, but in Drive
        r = gear_run([(self.A, minute(1, 1)), (self.B, light), ("2026-10-12_13-10-00", light), ("2026-10-12_13-11-00", minute(1, 1))])
        self.assertEqual([k for k, _ in r], ["drive"])

    def test_reverse_counts_as_in_gear(self):
        r = gear_run([(self.A, minute(2, 2, v=5)), (self.B, minute(2, 1))])
        self.assertEqual([k for k, _ in r], ["drive"])


if __name__ == "__main__":
    unittest.main()
