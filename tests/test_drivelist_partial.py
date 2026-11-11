"""test_drivelist_partial.py - a drive the dashcam recorded only part of, and drives Tessie has not given yet (11 Nov 2026: the JOWUA was plugged in part way through the morning drive).

Tessie's park between two of its events is cut where the dashcam saw the car out driving; a dashcam drive that starts (or ends) away from where the car was parked says where it
set off from (`head`) or was going (`tail`); and when Tessie's drives arrive they are the rows, as ever.

    python3 -m unittest tests/test_drivelist_partial.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_drivelist as DL  # noqa: E402
from test_drivelist import by, cam, charge, tess, T  # noqa: E402

HOME, SCHOOL, MARSFIELD, ROAD = [-33.80000, 151.00000], [-33.79783, 151.1116], [-33.77858, 151.1083], [-33.80880, 151.00983]      # ROAD: 1.3 km from Home, where the footage began
# the real morning, as seconds from the end of the evening charge: charge ends 0, drives 56059-56482, 56861-57447, 57606-58305, next charge 58419
EVENING = dict(lat=HOME[0], lon=HOME[1])
MORNING = [cam(1, 56059, 56482, p0=ROAD, p1=SCHOOL, km=3.3), cam(2, 56482, 56861, kind="stopped", p0=SCHOOL, p1=SCHOOL, km=0),
           cam(3, 56861, 57447, p0=SCHOOL, p1=MARSFIELD, km=4.0), cam(4, 57447, 57578, kind="stopped", p0=MARSFIELD, p1=MARSFIELD, km=0),
           cam(5, 57606, 58305, p0=MARSFIELD, p1=HOME, km=5.4)]
CHARGES = [charge(1, -5700, 0, **EVENING), charge(2, 58419, 59805, **EVENING)]


def overlap(a, b):
    return min(a["t1"], b["t1"]) - max(a["t0"], b["t0"])


class TessieLags(unittest.TestCase):
    def setUp(self):
        self.r = DL.merge([], CHARGES, [dict(c) for c in MORNING])

    def test_tessies_park_stops_where_the_dashcam_saw_the_car_drive_off(self):
        parks = [x for x in by(self.r, "parked") if x.get("src") == "tessie"]
        self.assertEqual([(x["t0"] - T, x["t1"] - T, x["p0"]) for x in parks], [(0, 56059, HOME)])      # Home until the footage begins, not until the next charge
        for d in by(self.r, "drive"):
            for p in by(self.r, "parked") + by(self.r, "gap"):
                self.assertLessEqual(overlap(d, p), 0, "%s lies over the drive %s" % (p["id"], d["id"]))

    def test_the_two_minutes_between_the_last_drive_and_the_charge_are_no_row(self):
        self.assertFalse([x for x in self.r if x["kind"] in ("parked", "gap") and x["t0"] >= T + 58305])

    def test_the_dashcams_drives_and_stops_are_all_listed(self):
        self.assertEqual(sorted(x["id"] for x in by(self.r, "drive")), ["c1", "c3", "c5"])
        self.assertEqual(sorted(x["id"] for x in by(self.r, "stopped")), ["c2", "c4"])

    def test_the_drive_that_began_before_the_footage_says_where_it_set_off_from(self):
        d = {x["id"]: x for x in by(self.r, "drive")}
        self.assertEqual(d["c1"]["head"]["p"], HOME)
        self.assertTrue(1000 < d["c1"]["head"]["m"] < 1600)
        self.assertNotIn("tail", d["c1"])
        for k in ("c3", "c5"):                                  # these began where the car had stopped, and the last ended where it then charged
            self.assertNotIn("head", d[k])
            self.assertNotIn("tail", d[k])

    def test_when_tessie_brings_its_drives_they_are_the_rows(self):
        td = [tess(1, 55631, 56485, lat=HOME[0], lon=HOME[1], lat1=SCHOOL[0], lon1=SCHOOL[1], frm="Home", to="School", km=6.69),
              tess(2, 56862, 57412, lat=SCHOOL[0], lon=SCHOOL[1], lat1=MARSFIELD[0], lon1=MARSFIELD[1], frm="School", to="Marsfield"),
              tess(3, 57628, 58328, lat=MARSFIELD[0], lon=MARSFIELD[1], lat1=HOME[0], lon1=HOME[1], frm="Marsfield", to="Home")]
        r = DL.merge(td, CHARGES, [dict(c) for c in MORNING])
        d = sorted(by(r, "drive"), key=lambda x: x["t0"])
        self.assertEqual([(x["id"], x["src"], x["cam"]) for x in d], [("d1", "both", ["c1"]), ("d2", "both", ["c3"]), ("d3", "both", ["c5"])])
        self.assertEqual(d[0]["cov"], 0.5)                      # the footage has the second half of it
        self.assertFalse(any("head" in x or "tail" in x for x in d))
        home = [x for x in by(r, "parked") if x["t0"] == T]
        self.assertEqual(home[0]["t1"], T + 55631)              # parked at Home until Tessie's drive began


class Alone(unittest.TestCase):
    def test_a_shuffle_the_dashcam_saw_does_not_cut_tessies_park(self):
        r = DL.merge([], CHARGES, [cam(1, 30000, 30040, p0=HOME, p1=HOME, km=0.02)])
        parks = [x for x in by(r, "parked") if x.get("src") == "tessie"]
        self.assertEqual([(x["t0"] - T, x["t1"] - T) for x in parks], [(0, 58419)])

    def test_with_the_dashcam_alone_the_park_before_it_says_where_the_drive_began(self):
        r = DL.merge([], [], [cam(0, -6000, -600, kind="parked", p0=HOME, p1=HOME, km=0)] + [dict(c) for c in MORNING])
        d = {x["id"]: x for x in by(r, "drive")}
        self.assertEqual(d["c1"]["head"]["p"], HOME)

    def test_footage_that_stops_before_the_drive_did_says_where_it_was_going(self):
        r = DL.merge([], [charge(2, 2000, 4000, **EVENING)], [cam(0, -3000, 0, kind="parked", p0=SCHOOL, p1=SCHOOL, km=0), cam(1, 0, 600, p0=SCHOOL, p1=ROAD, km=3.0)])
        d = by(r, "drive")[0]
        self.assertEqual(d["tail"]["p"], HOME)
        self.assertNotIn("head", d)

    def test_a_drive_far_from_where_the_car_last_was_is_another_trip_not_a_missing_start(self):
        far = [-34.9, 150.6]
        r = DL.merge([], [], [cam(0, -6000, -600, kind="parked", p0=HOME, p1=HOME, km=0), cam(1, 0, 900, p0=far, p1=[-34.95, 150.65], km=8.0)])
        self.assertNotIn("head", by(r, "drive")[0])


class Names(unittest.TestCase):
    def test_what_only_the_dashcam_saw_gets_the_suburb_of_where_it_was(self):
        r = DL.merge([], CHARGES, [dict(c) for c in MORNING])
        seen = []

        def label(lat, lon):
            seen.append((lat, lon))
            return {tuple(MARSFIELD): "Marsfield, NSW", tuple(ROAD): "West Ryde, NSW"}.get((lat, lon), "")
        DL.name_unnamed(r, label, T + 60000)
        d = {x["id"]: x for x in r}
        self.assertEqual((d["c1"].get("at0"), d["c1"].get("at1")), ("West Ryde, NSW", None))         # no suburb known for the school yet: nothing is made up
        self.assertEqual(d["c3"].get("at1"), "Marsfield, NSW")
        self.assertEqual(d["c4"].get("at"), "Marsfield, NSW")                                         # the stop there
        self.assertFalse(any("at" in x for x in r if x.get("src") == "tessie"))                        # Tessie's own rows are not asked about

    def test_old_rows_are_left_alone(self):
        r = DL.merge([], [], [cam(1, 0, 900, p0=ROAD, p1=MARSFIELD)])
        DL.name_unnamed(r, lambda la, lo: "X, NSW", T + 900 + (DL.NAME_DAYS + 1) * 86400)
        self.assertNotIn("at0", r[0])


class SuburbLabel(unittest.TestCase):
    def test_the_label_is_the_suburb_and_the_state_and_empty_until_the_suburb_is_known(self):
        import tempfile
        import app_teslacam
        tc = app_teslacam.TeslaCam(tempfile.mkdtemp())

        class Subs:
            def at(self, lat, lon):
                return "Marsfield" if lat > -33.79 else None
        tc.__dict__["_suburbs"] = Subs()
        self.assertEqual(tc.suburb_label(MARSFIELD[0], MARSFIELD[1]), "Marsfield, NSW")
        self.assertEqual(tc.suburb_label(HOME[0], HOME[1]), "")
        self.assertEqual(tc.suburb_label(None, None), "")


if __name__ == "__main__":
    unittest.main()
