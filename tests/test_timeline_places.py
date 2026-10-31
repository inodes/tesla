"""test_timeline_places.py - where the stops the dashcam made while parked were (they carry no GPS)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import teslacam_timeline as TL  # noqa: E402

H = 3600
HOME, SHOP = [-33.81, 151.11], [-33.50, 151.00]


def drive(t0, t1, p0=SHOP, p1=HOME):
    return dict(id="d%d" % t0, kind="drive", t0=t0, t1=t1, p0=p0, p1=p1)


def stop(t0, t1, kind="stopped"):
    return dict(id="s%d" % t0, kind=kind, t0=t0, t1=t1, p0=None, p1=None)


def by_id(items):
    return {x["id"]: x for x in items}


class PlacesTest(unittest.TestCase):
    def test_a_stop_beside_a_drive_is_where_the_drive_ended_or_began_and_no_estimate(self):
        items = [stop(2 * H, 2 * H + 600), drive(H, 2 * H - 60), stop(5 * H - 120, 5 * H), drive(5 * H + 60, 6 * H, p0=HOME, p1=SHOP)]
        r = by_id(TL.fill_places(items))
        self.assertEqual((r["s%d" % (2 * H)]["p0"], r["s%d" % (2 * H)]["pest"]), (HOME, False))
        self.assertEqual((r["s%d" % (5 * H - 120)]["p1"], r["s%d" % (5 * H - 120)]["pest"]), (HOME, False))

    def test_a_stop_far_from_any_drive_is_an_estimate_from_the_last_known_place(self):
        items = [drive(H, 2 * H), stop(10 * H, 10 * H + 60), stop(20 * H, 20 * H + 60), stop(40 * H, 40 * H + 60)]
        r = by_id(TL.fill_places(items))
        self.assertEqual((r["s%d" % (10 * H)]["p0"], r["s%d" % (10 * H)]["pest"]), (HOME, True))
        self.assertEqual((r["s%d" % (20 * H)]["p0"], r["s%d" % (20 * H)]["pest"]), (HOME, True))      # a chain of estimates stays one
        self.assertIsNone(r["s%d" % (40 * H)]["p0"])                                                  # more than a day: not guessed

    def test_tessie_confirms_or_corrects_the_estimate(self):
        items = [drive(H, 2 * H), stop(10 * H, 10 * H + 60)]
        nothing = [dict(t0=0, t1=100, p0=SHOP, p1=SHOP)]
        r = by_id(TL.fill_places(items, nothing, 11 * H))
        self.assertEqual((r["s%d" % (10 * H)]["p0"], r["s%d" % (10 * H)]["pest"]), (HOME, False))      # complete to here, no drive since: confirmed
        r = by_id(TL.fill_places(items, nothing, 5 * H))
        self.assertTrue(r["s%d" % (10 * H)]["pest"])                                                  # not complete to here: still an estimate
        moved = [dict(t0=3 * H, t1=4 * H, p0=HOME, p1=SHOP)]
        r = by_id(TL.fill_places(items, moved, 11 * H))
        self.assertEqual((r["s%d" % (10 * H)]["p0"], r["s%d" % (10 * H)]["pest"]), (SHOP, False))      # it moved without a clip showing it

    def test_a_position_already_known_is_kept(self):
        it = dict(stop(H, H + 60), p0=SHOP, p1=SHOP)
        self.assertEqual(TL.fill_places([it, drive(2 * H, 3 * H)])[0]["p0"], SHOP)


if __name__ == "__main__":
    unittest.main()
