"""test_timeline_seconds.py - a drive is cut to the second by the car's own gear: shifting into Park ends it, a shift out of Park and back with no movement (a blip) is nothing,
and what follows a Park is a short re-park (a real, short move) or an actual drive.

    python3 -m unittest tests/test_timeline_seconds.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import teslacam_timeline as TL  # noqa: E402

LAT = [-32.0]


def spec(txt):
    """'D40@50 P20' -> 60 (gear, km/h) seconds: Drive 40 s at 50 km/h, Park 20 s. Letters P D R N."""
    out = []
    for w in txt.split():
        g = "PDRN".index(w[0])
        n, _, v = w[1:].partition("@")
        out += [(g, float(v or 0))] * int(n)
    assert len(out) == 60, len(out)
    return out


class SecStore:
    def __init__(self, minutes):
        self.m = minutes

    def summary(self, stamp):
        m = self.m.get(stamp)
        if m is None:
            return None
        v = [x[1] for x in m]
        return dict(n=60, vmax=max(v), km=round(sum(v) / 3600.0, 3), mov=sum(1 for x in v if x >= 3), fsd=0, aut=0, p0=[-32.0, 151.0], p1=[-32.0, 151.0])

    def series(self, stamp):
        m = self.m.get(stamp)
        if not m:
            return None
        lat, la = [], -32.0
        for g, v in m:
            la -= v / 3.6 / 111320.0
            lat.append(la)
        return dict(gear=[g for g, _ in m], v=[v for _, v in m], lat=lat, lon=[151.0] * 60, ap=[0] * 60, hd=[0] * 60, steer=[0] * 60, accel=[0] * 60, br=[0] * 60, blk=[0] * 60)


def stamp(n):
    return "2026-10-12_13-%02d-00" % n


def items(minutes):
    st = SecStore({stamp(n): spec(t) for n, t in minutes})
    out = TL.build({stamp(n): ["front"] for n, _ in minutes}, [], [], st)
    return sorted([i for i in out if i["kind"] in ("drive", "stopped")], key=lambda i: i["t0"])


def kinds(r):
    return [i["kind"] for i in r]


class Cut(unittest.TestCase):
    def test_drive_ends_the_second_it_goes_into_park(self):
        r = items([(8, "D60@50"), (9, "D40@50 P20")])
        self.assertEqual(kinds(r), ["drive"])
        self.assertEqual(r[0]["t1"] - r[0]["t0"], 100)

    def test_drive_starts_the_second_it_leaves_park(self):
        r = items([(8, "P60"), (9, "P25 D35@50")])
        self.assertEqual(kinds(r), ["stopped", "drive"])
        self.assertEqual(r[1]["t1"] - r[1]["t0"], 35)
        self.assertEqual(r[0]["t1"], r[1]["t0"])                                       # the stop runs up to the second the drive began

    def test_a_blip_between_two_drives_is_nothing(self):
        r = items([(8, "D40@50 P20"), (9, "P20 R5 P10 D25@50"), (10, "D60@50")])
        self.assertEqual(kinds(r), ["drive", "drive"])                                  # Park ended the first; the blip is not a drive; the second drive is real
        self.assertEqual(r[0]["t1"] - r[0]["t0"], 40)
        self.assertEqual(r[1]["t1"] - r[1]["t0"], 25 + 60)

    def test_a_blip_in_a_long_stop_is_one_stop(self):
        r = items([(8, "P60"), (9, "P20 D5 P35"), (10, "P60")])
        self.assertEqual(kinds(r), ["stopped"])

    def test_a_short_re_park_is_a_drive_of_its_own(self):
        r = items([(8, "D50@50 P10"), (9, "P20 D12@8 P28")])
        self.assertEqual(kinds(r), ["drive", "drive"])
        self.assertEqual(r[1]["t1"] - r[1]["t0"], 12)
        self.assertTrue(r[1]["short"])
        self.assertFalse(r[0]["short"])

    def test_a_whole_minute_in_park_between_two_drives_is_a_stop_not_a_red_light(self):
        r = items([(8, "D60@50"), (9, "P60"), (10, "D60@50")])
        self.assertEqual(kinds(r), ["drive", "stopped", "drive"])

    def test_a_minute_with_no_telemetry_after_park_is_the_stop(self):
        st = SecStore({stamp(8): spec("D40@50 P20"), stamp(10): spec("P3 D57@50")})                    # the car writes no telemetry in Park for long: minute 9 has none
        st.m[stamp(9)] = None
        st.summary = lambda s, f=st.summary: 0 if s == stamp(9) else f(s)
        out = TL.build({stamp(n): ["front"] for n in (8, 9, 10)}, [], [], st)
        self.assertEqual(sorted(i["kind"] for i in out if i["kind"] in ("drive", "stopped")), ["drive", "drive", "stopped"])

    def test_a_minute_with_no_telemetry_inside_a_drive_does_not_split_it(self):
        st = SecStore({stamp(8): spec("D60@50"), stamp(10): spec("D60@50")})
        st.summary = lambda s, f=st.summary: 0 if s == stamp(9) else f(s)
        out = TL.build({stamp(n): ["front"] for n in (8, 9, 10)}, [], [], st)
        self.assertEqual([i["kind"] for i in out if i["kind"] in ("drive", "stopped")], ["drive"])

    def test_a_light_in_drive_is_still_the_drive(self):
        r = items([(8, "D60@50"), (9, "D60"), (10, "D60@50")])
        self.assertEqual(kinds(r), ["drive"])

    def test_ids_are_unique_when_two_drives_start_in_one_minute(self):
        r = items([(8, "D10@30 P10 D10@30 P10 D10@30 P10")])
        self.assertEqual(kinds(r), ["drive", "drive", "drive"])
        self.assertEqual(len({i["id"] for i in r}), 3)

    def test_the_drive_is_found_again_by_its_id(self):
        r = items([(8, "D10@30 P10 D10@30 P30")])
        for i in r:
            self.assertTrue(i["t0"] <= TL.secs(i["id"]) < i["t1"])

    def test_numbers_are_from_the_seconds_of_the_drive_only(self):
        r = items([(8, "D30@36 P30")])
        self.assertEqual(r[0]["vmax"], 36)
        self.assertAlmostEqual(r[0]["km"], 0.3, places=1)                               # 30 s at 36 km/h = 300 m


if __name__ == "__main__":
    unittest.main()
