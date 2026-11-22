"""Places "Stops to review": the parked rows of the Drives list that show "Save", per spot, each with the drive before and after (app_places.Places.review_stops)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
import app_places as AP  # noqa: E402

HOME = (-33.80659, 151.0796)
BEVIN = (-33.86654, 151.1216)
GOSF = (-33.4252, 151.32)


def drive(i, t0, t1, frm, to, p0, p1):
    return dict(kind="drive", id="d%d" % i, t0=str(t0), t1=str(t1), frm=frm, to=to, p0=list(p0), p1=list(p1), start="2026-11-22T%02d:00:00" % i, end="", km="5.0")


def park(i, t0, t1, p, day="2026-11-22"):
    return dict(kind="parked", id="g:%d" % t0, t0=str(t0), t1=str(t1), p0=list(p), p1=list(p), start="%sT%02d:00:00" % (day, i), end="%sT%02d:30:00" % (day, i))


class ReviewStopsTest(unittest.TestCase):
    def setUp(self):
        self.P = AP.Places.__new__(AP.Places)
        self.P.defs = lambda: [dict(name="Home", lat=HOME[0], lon=HOME[1], radius=100)]
        self.P.f_ignored = os.path.join(tempfile.mkdtemp(), "ignored_places.json")
        self.items = [
            park(8, 1000, 2000, HOME),                                   # at a saved place: "Saved"
            drive(9, 2000, 2600, "Home", "11 Bevin Avenue, Five Dock, NSW", HOME, BEVIN),
            park(10, 2600, 6800, BEVIN),                                 # shows "Save"
            drive(11, 6800, 7600, "11 Bevin Avenue, Five Dock, NSW", "Gosford", BEVIN, GOSF),
            park(12, 7600, 9000, GOSF),                                  # charged while there: the charge, not "Save"
            drive(13, 9000, 9900, "Gosford", "Home", GOSF, HOME),
            park(14, 9900, 12000, BEVIN, day="2026-08-15"),              # same spot, another day, no drive after it
        ]
        self.chg = [dict(t0=7700, t1=8800, lat=GOSF[0], lon=GOSF[1])]

    def test_only_parks_that_show_save_are_listed_per_spot(self):
        r = self.P.review_stops(self.items, self.chg)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["visits"], r[0]["days"], len(r[0]["parks"]), r[0]["addr"]), (2, 2, 2, "11 Bevin Avenue, Five Dock, NSW"))
        self.assertEqual(r[0]["key"], AP.spot_key(r[0]["lat"], r[0]["lon"]))

    def test_each_park_has_the_drive_before_and_after(self):
        parks = self.P.review_stops(self.items, self.chg)[0]["parks"]
        first = next(k for k in parks if k["t0"] == 2600)
        self.assertEqual((first["before"]["id"], first["after"]["id"]), ("d9", "d11"))
        self.assertEqual(first["min"], 70)
        last = next(k for k in parks if k["t0"] == 9900)
        self.assertEqual(last["before"]["id"], "d13")
        self.assertIsNone(last["after"])

    def test_a_spot_saved_since_drops_out(self):
        self.P.defs = lambda: [dict(name="Home", lat=HOME[0], lon=HOME[1], radius=100), dict(name="Canada Bay Club", lat=BEVIN[0], lon=BEVIN[1], radius=150)]
        self.assertEqual(self.P.review_stops(self.items, self.chg), [])


if __name__ == "__main__":
    unittest.main()
