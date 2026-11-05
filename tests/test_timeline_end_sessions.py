"""A session that ends parked, with no footage for a long time after it, ends at its last drive in the TeslaCam timeline (the Drives list keeps the park)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import teslacam_timeline as TL  # noqa: E402

H = 3600


def it(kind, t0, t1, i=None):
    return dict(id=i or "%s:%d" % (kind, t0), kind=kind, t0=t0, t1=t1)


class EndSessionsTest(unittest.TestCase):
    def test_a_night_parked_after_a_drive_is_left_out_with_the_stopped_minutes_before_it(self):
        out = [it("drive", 0, H), it("stopped", H, H + 300), it("parked", H + 300, 16 * H, "g:1"), it("stopped", 16 * H, 16 * H + 60), it("drive", 16 * H + 60, 17 * H)]
        got = [x["kind"] for x in TL.end_sessions(out)]
        self.assertEqual(got, ["drive", "stopped", "drive"])

    def test_a_shop_or_a_drop_off_is_kept(self):
        for hours in (0.1, 3, 5.9):
            out = [it("drive", 0, H), it("parked", H, H + int(hours * H), "g:1"), it("drive", H + int(hours * H), 3 * H + int(hours * H))]
            self.assertEqual([x["kind"] for x in TL.end_sessions(out)], ["drive", "parked", "drive"], hours)

    def test_a_short_park_with_a_sentry_event_in_it_is_kept_whole(self):
        out = [it("drive", 0, H), it("parked", H, 3 * H, "g:1"), it("sentry", 3 * H, 3 * H + 60), it("parked", 3 * H + 60, 5 * H, "g:2"), it("drive", 5 * H, 6 * H)]
        self.assertEqual(len(TL.end_sessions(out)), 5)

    def test_a_night_with_sentry_events_keeps_them_and_the_stopped_minutes_up_to_them(self):
        out = [it("drive", 0, H), it("stopped", H, H + 300), it("parked", H + 300, 9 * H, "g:1"), it("sentry", 9 * H, 9 * H + 60), it("sentry", 9 * H + 600, 9 * H + 660),
               it("parked", 9 * H + 660, 24 * H, "g:2"), it("drive", 24 * H, 25 * H)]
        got = [x["id"] if x["kind"] in ("parked",) else x["kind"] for x in TL.end_sessions(out)]
        self.assertEqual(got, ["drive", "stopped", "sentry", "sentry", "drive"])

    def test_a_night_that_begins_with_sentry_events_ends_where_the_car_sleeps(self):
        out = [it("sentry", 0, 60), it("sentry", 600, 660), it("parked", 660, 10 * H, "g:1"), it("drive", 10 * H, 11 * H), it("parked", 11 * H, 13 * H, "g:2"), it("drive", 13 * H, 14 * H)]
        self.assertEqual([x["kind"] for x in TL.end_sessions(out)], ["sentry", "sentry", "drive", "parked", "drive"])

    def test_a_long_hole_with_no_known_place_is_no_row_in_the_footage_timeline_either(self):
        out = [it("drive", 0, H), it("gap", H, 16 * H, "g:1"), it("drive", 16 * H, 17 * H)]
        self.assertEqual([x["kind"] for x in TL.end_sessions(out)], ["drive", "drive"])

    def test_a_short_hole_is_kept(self):
        out = [it("drive", 0, H), it("gap", H, 2 * H, "g:1"), it("drive", 2 * H, 3 * H)]
        self.assertEqual(len(TL.end_sessions(out)), 3)

    def test_the_limit_comes_from_the_options(self):
        real = TL.OPTS["park_end_s"]
        self.addCleanup(lambda: TL.OPTS.update(park_end_s=real))
        TL.OPTS["park_end_s"] = 2 * H
        out = [it("drive", 0, H), it("parked", H, 4 * H, "g:1"), it("drive", 4 * H, 5 * H)]
        self.assertEqual(len(TL.end_sessions(out)), 2)

    def test_a_drive_page_gap_is_still_built_for_the_drives_list(self):
        a, b = dict(t0=0, t1=H, p1=[-33.8, 151.1]), dict(t0=16 * H, t1=17 * H, p0=[-33.8, 151.1])
        self.assertEqual(TL._gap(a, b)["kind"], "parked")


if __name__ == "__main__":
    unittest.main()
