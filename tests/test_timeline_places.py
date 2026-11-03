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

    def test_a_stop_between_two_drives_at_one_place_is_confirmed_by_both(self):
        items = [drive(H, 2 * H - 60), stop(2 * H, 2 * H + 3 * H), drive(5 * H + 60, 6 * H, p0=HOME, p1=SHOP)]
        r = by_id(TL.fill_places(items))["s%d" % (2 * H)]
        self.assertEqual((r["p0"], r["pest"], r["psrc"]), (HOME, False, "both"))

    def test_drives_that_disagree_give_the_one_before_and_say_so(self):
        items = [drive(H, 2 * H - 60), stop(2 * H, 3 * H), drive(3 * H + 60, 4 * H, p0=SHOP, p1=HOME)]
        r = by_id(TL.fill_places(items))["s%d" % (2 * H)]
        self.assertEqual((r["p0"], r["psrc"]), (HOME, "before"))
        r = by_id(TL.fill_places([stop(2 * H, 3 * H), drive(3 * H + 60, 4 * H, p0=SHOP, p1=HOME)]))["s%d" % (2 * H)]
        self.assertEqual((r["p0"], r["psrc"]), (SHOP, "after"))

    def test_a_stop_reads_on_the_clock_of_the_place_it_was_at(self):
        gold_coast, sydney_t = [-27.98, 153.42], 1791693034                    # 11 Oct 2026 04:30 UTC: Queensland 14:30, Sydney 15:30
        s0 = TL.show(dict(id="s", kind="stopped", t0=sydney_t, t1=sydney_t + 600, p0=None, p1=None))
        self.assertEqual(s0["start"][11:16], "15:30")                          # no position: the app's clock
        d = dict(id="d", kind="drive", t0=sydney_t + 600, t1=sydney_t + 3600, p0=gold_coast, p1=gold_coast)
        r = by_id(TL.fill_places([s0, d]))["s"]
        self.assertEqual((r["start"][11:16], r["psrc"]), ("14:30", "after"))   # the drive after it began at the Gold Coast

    def test_stops_that_follow_on_are_one_stay(self):
        items = [stop(H, H + 600), stop(H + 660, H + 1200), drive(H + 1260, 3 * H, p0=SHOP, p1=HOME)]
        r = by_id(TL.fill_places(items))
        self.assertEqual((r["s%d" % H]["p0"], r["s%d" % H]["psrc"]), (SHOP, "stay"))
        self.assertEqual((r["s%d" % (H + 660)]["p0"], r["s%d" % (H + 660)]["psrc"]), (SHOP, "after"))
        far = [stop(H, H + 600), stop(H + 5 * H, H + 6 * H), drive(H + 6 * H + 60, 9 * H, p0=SHOP, p1=HOME)]
        self.assertIsNone(by_id(TL.fill_places(far))["s%d" % H]["p0"])                   # hours apart: not one stay

    def test_a_position_already_known_is_kept(self):
        it = dict(stop(H, H + 60), p0=SHOP, p1=SHOP)
        self.assertEqual(TL.fill_places([it, drive(2 * H, 3 * H)])[0]["p0"], SHOP)


if __name__ == "__main__":
    unittest.main()
