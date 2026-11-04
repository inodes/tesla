"""test_drivelist.py - the Drives list: Tessie's drives are the rows when there are any, the dashcam's drives attach to them by time, and with no Tessie the dashcam alone makes the list.

    python3 -m unittest tests/test_drivelist.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_drivelist as DL  # noqa: E402

T = 1792000000          # a UTC second; everything is relative to it


def tess(i, a, b, **kw):
    r = dict(k="drive", id="d%d" % i, t0=T + a, t1=T + b, day="2026-10-12", hm="10:00", tz="AEDT", km=5.0, max=60, kwh=1.0, whkm=150, b0=80, b1=78, ap=0, lat=-33.8, lon=151.2,
             lat1=-33.9, lon1=151.3, frm="A", to="B", min=(b - a) / 60.0, repark=False, page=True, al=0)
    r.update(kw)
    return r


def cam(i, a, b, kind="drive", **kw):
    r = dict(id="c%d" % i, kind=kind, t0=T + a, t1=T + b, km=4.8, vmax=58, p0=[-33.8, 151.2], p1=[-33.9, 151.3], n=1, start="", end="")
    r.update(kw)
    return r


def charge(i, a, b, **kw):
    r = dict(k="charge", id="ch%d" % i, t0=T + a, t1=T + b, day="2026-10-12", hm="12:00", tz="AEDT", min=(b - a) / 60.0, place="Home", kwh=10.0, b0=40, b1=60, kind="ac", cost=2.0, lat=-33.9, lon=151.3)
    r.update(kw)
    return r


def by(items, kind):
    return [i for i in items if i["kind"] == kind]


class Merge(unittest.TestCase):
    def test_a_tessie_drive_is_the_row_and_the_dashcam_attaches(self):
        r = DL.merge([tess(1, 0, 600)], [], [cam(1, 30, 590)])
        d = by(r, "drive")
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["id"], d[0]["t0"], d[0]["t1"], d[0]["km"]), ("d1", T, T + 600, 5.0))     # Tessie's id, times and distance
        self.assertEqual(d[0]["cam"], ["c1"])
        self.assertEqual(d[0]["src"], "both")
        self.assertGreater(d[0]["cov"], 0.9)

    def test_two_dashcam_drives_in_one_tessie_drive_stay_one_row(self):
        r = DL.merge([tess(1, 0, 600)], [], [cam(1, 5, 200), cam(2, 260, 598)])
        d = by(r, "drive")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["cam"], ["c1", "c2"])

    def test_tessie_times_are_minute_rounded_so_a_close_edge_still_attaches(self):
        r = DL.merge([tess(1, 0, 600)], [], [cam(1, 590, 640)])                          # starts 10 s before Tessie's end and runs 40 s past it
        self.assertEqual(len(by(r, "drive")), 1)
        self.assertEqual(by(r, "drive")[0]["cam"], ["c1"])

    def test_a_tessie_drive_without_footage_is_still_a_row(self):
        r = DL.merge([tess(1, 0, 600)], [], [])
        d = by(r, "drive")
        self.assertEqual((len(d), d[0]["cam"], d[0]["src"], d[0]["cov"]), (1, [], "tessie", 0))

    def test_a_dashcam_drive_tessie_never_recorded_is_listed_as_dashcam_only(self):
        r = DL.merge([tess(1, 0, 600)], [], [cam(1, 0, 600), cam(2, 5000, 5400)])
        d = sorted(by(r, "drive"), key=lambda x: x["t0"])
        self.assertEqual([x["src"] for x in d], ["both", "cam"])
        self.assertEqual(d[1]["id"], "c2")

    def test_a_dashcam_drive_goes_to_the_tessie_drive_it_overlaps_most(self):
        r = DL.merge([tess(1, 0, 300), tess(2, 330, 900)], [], [cam(1, 340, 880)])
        d = {x["id"]: x for x in by(r, "drive")}
        self.assertEqual((d["d1"]["cam"], d["d2"]["cam"]), ([], ["c1"]))

    def test_without_tessie_the_dashcam_makes_the_list(self):
        r = DL.merge([], [], [cam(1, 0, 600), cam(2, 900, 1200)])
        d = by(r, "drive")
        self.assertEqual((len(d), {x["src"] for x in d}), (2, {"cam"}))

    def test_a_stop_during_a_charge_is_the_charge_row(self):
        r = DL.merge([tess(1, 0, 600)], [charge(1, 700, 4000)], [cam(1, 0, 600), cam(2, 650, 4100, kind="stopped")])
        self.assertEqual(len(by(r, "charge")), 1)
        self.assertEqual(by(r, "stopped"), [])
        self.assertEqual(by(r, "charge")[0]["id"], "ch1")

    def test_a_stop_that_is_not_a_charge_stays(self):
        r = DL.merge([], [charge(1, 90000, 93000)], [cam(2, 650, 4100, kind="stopped")])
        self.assertEqual(len(by(r, "stopped")), 1)

    def test_sentry_events_are_kept(self):
        r = DL.merge([tess(1, 0, 600)], [], [cam(3, 700, 760, kind="sentry", ev="e1")])
        self.assertEqual(len(by(r, "sentry")), 1)

    def test_newest_first_and_display_times_are_set(self):
        r = DL.merge([tess(1, 0, 600), tess(2, 5000, 5600)], [], [])
        self.assertEqual([x["id"] for x in r if x["kind"] == "drive"], ["d2", "d1"])
        self.assertTrue(r[0]["start"] and r[0]["end"] and "lo" in r[0])

    def test_a_tessie_re_park_is_marked_short(self):
        r = DL.merge([tess(1, 0, 60, repark=True, km=0.05)], [], [])
        self.assertTrue(by(r, "drive")[0]["short"])


class Parked(unittest.TestCase):
    def test_the_time_between_two_tessie_drives_at_one_place_is_a_parked_row(self):
        r = DL.merge([tess(1, 0, 600, lat1=-33.9, lon1=151.3), tess(2, 5000, 5600, lat=-33.9, lon=151.3)], [], [])
        p = by(r, "parked")
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["t0"], p[0]["t1"], p[0]["src"]), (T + 600, T + 5000, "tessie"))

    def test_a_hole_between_drives_elsewhere_is_not_parked(self):
        r = DL.merge([tess(1, 0, 600, lat1=-33.9, lon1=151.3), tess(2, 5000, 5600, lat=-34.5, lon=150.9)], [], [])
        self.assertEqual(by(r, "parked"), [])

    def test_dashcam_stops_inside_a_parked_row_are_not_listed_again(self):
        r = DL.merge([tess(1, 0, 600, lat1=-33.9, lon1=151.3), tess(2, 5000, 5600, lat=-33.9, lon=151.3)], [], [cam(5, 700, 1500, kind="stopped"), cam(6, 1600, 3000, kind="stopped")])
        self.assertEqual(len(by(r, "parked")) + len(by(r, "stopped")), 1)

    def test_a_parked_row_of_tessie_carries_the_footage_of_the_stops_inside_it(self):          # 4 Nov: the park at the preschool had footage, but its row had nothing to open
        r = DL.merge([tess(1, 0, 600, lat1=-33.9, lon1=151.3), tess(2, 5000, 5600, lat=-33.9, lon=151.3)], [], [cam(5, 700, 1500, kind="stopped"), cam(6, 1600, 3000, kind="stopped")])
        self.assertEqual(by(r, "parked")[0]["cam"], ["c5", "c6"])

    def test_idle_energy_and_sentry_are_put_on_the_parked_row(self):
        idle = dict(k="idle", t0=T + 1000, t1=T + 4000, kwh=0.8, sentry=1.0)
        r = DL.merge([tess(1, 0, 600, lat1=-33.9, lon1=151.3), tess(2, 5000, 5600, lat=-33.9, lon=151.3)], [], [], [idle])
        self.assertEqual((by(r, "parked")[0]["kwh"], by(r, "parked")[0]["sentry"]), (0.8, 1.0))

    def test_without_tessie_dashcam_stops_that_follow_each_other_are_one_spell(self):
        r = DL.merge([], [], [cam(1, 0, 600, kind="stopped", p1=[-33.8, 151.2]), cam(2, 640, 1300, kind="stopped"), cam(3, 5000, 5600, kind="stopped")])
        s = sorted(by(r, "stopped"), key=lambda x: x["t0"])
        self.assertEqual([(x["t0"], x["t1"]) for x in s], [(T, T + 1300), (T + 5000, T + 5600)])

    def test_missing_counts_dashcam_only_drives_and_recent_tessie_drives_with_no_page(self):
        r = DL.merge([tess(1, 0, 600), tess(2, 5000, 5600, page=False)], [], [cam(1, 0, 600), cam(2, 9000, 9400), cam(3, 12000, 12020, km=0.05)])
        m = DL.missing(r, T + 20000)
        self.assertEqual((m["cam_only"], m["no_page"]), (1, 1))
        self.assertEqual(m["since"], T + 5000)
        self.assertEqual(len(m["ids"]), 2)

    def test_missing_counts_old_tessie_drives_too_but_not_short_moves(self):
        r = DL.merge([tess(1, 0, 600, page=False), tess(2, 5000, 5600, short=True, repark=True, page=False)], [], [])
        self.assertEqual(DL.missing(r, T + 40 * 86400)["no_page"], 1)
        self.assertEqual(DL.missing(r, T + 20000)["no_page"], 1)
        self.assertEqual(DL.missing(r, T + 40 * 86400, recent_days=2)["no_page"], 0)

    def test_nothing_missing_when_tessie_has_it_all(self):
        r = DL.merge([tess(1, 0, 600)], [], [cam(1, 0, 600)])
        m = DL.missing(r, T + 1000)
        self.assertEqual((m["cam_only"], m["no_page"], m["since"]), (0, 0, None))

    def test_a_drive_already_tried_by_a_sync_is_not_counted_again(self):
        r = DL.merge([tess(2, 5000, 5600, page=False)], [], [cam(2, 9000, 9400)])
        ids = DL.missing(r, T + 20000)["ids"]
        m = DL.missing(r, T + 20000, tried=set(ids))
        self.assertEqual((m["cam_only"], m["no_page"], m["tried"], m["ids"]), (0, 0, 2, []))
        self.assertEqual((m["tried_cam"], m["tried_no_page"]), (1, 1))                  # said apart: "Tessie has no record" is true of one, "no route" of the other

    def test_a_changed_source_gives_the_old_list_at_once_and_rebuilds_in_the_background(self):
        import threading
        import time
        c, gate, n = DL.Cached(), threading.Event(), [0]

        def build():
            n[0] += 1
            if n[0] > 1:
                gate.wait(5)
            return dict(items=[n[0]])
        self.assertEqual(c.get("a", build)["items"], [1])
        self.assertEqual(c.get("a", build)["items"], [1])                    # unchanged: not built again
        got = c.get("b", build)                                              # changed: the old list, marked, without waiting
        self.assertEqual((got["items"], got["stale"]), ([1], True))
        gate.set()
        for _ in range(100):
            if c.get("b", build).get("stale") is None:
                break
            time.sleep(0.02)
        self.assertEqual(c.get("b", build)["items"], [2])

    def test_the_last_list_is_kept_in_a_file_for_the_next_run(self):
        import os
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "x", "drivelist.json")
        DL.Cached(path).get("a", lambda: dict(items=[7]))
        again = DL.Cached(path)
        self.assertEqual(again.get("z", lambda: dict(items=[8]), wait=False)["items"], [7])      # shown at once, stale
        self.assertEqual(again.get("z", lambda: dict(items=[8]), wait=True)["items"], [8])


class JoinParksTest(unittest.TestCase):
    H = [-33.8700, 151.2100]

    def _it(self, id, kind, t0, t1, **kw):
        return dict(id=id, kind=kind, t0=t0, t1=t1, p0=kw.pop("p0", self.H), p1=kw.pop("p1", self.H), **kw)

    def test_parks_and_a_reparks_at_one_place_are_one_park_and_the_newest_is_ongoing(self):
        items = [self._it("c", "stopped", 2040, 3500, p0=None, p1=None), self._it("m", "drive", 2000, 2030, short=True),
                 self._it("p", "parked", 1000, 2000), self._it("d", "drive", 0, 900, km=5.0)]
        out = DL.join_parks(items)
        self.assertEqual([x["kind"] for x in out], ["parked", "drive"])
        self.assertEqual((out[0]["t0"], out[0]["t1"]), (1000, 3500))
        self.assertTrue(out[0]["ongoing"])
        self.assertNotIn("ongoing", out[1])
        self.assertEqual(out[0]["moves"], ["m"])

    def test_a_reparks_on_arriving_is_where_the_stay_began(self):
        items = [self._it("p", "parked", 1060, 5000), self._it("m", "drive", 1000, 1060, short=True), self._it("d", "drive", 0, 1000, km=5.0, p1=self.H)]
        out = DL.join_parks(items)
        self.assertEqual([x["kind"] for x in out], ["parked", "drive"])
        self.assertEqual((out[0]["t0"], out[0]["t1"]), (1000, 5000))
        self.assertEqual(out[0]["moves"], ["m"])

    def test_a_short_move_after_a_drive_at_a_charger_is_the_end_of_that_drive(self):
        # 22:18-22:25 drive, 22:25-22:26 shuffle into the bay, then the charge: one drive, not a drive and a "short move"
        A, B = [-30.3098, 153.0977], [-30.2813, 153.1316]
        items = [self._it("c", "charge", 1060, 3000, p0=B, p1=B), self._it("m", "drive", 1000, 1060, short=True, p0=B, p1=B, km=0.06, frm="Plaza", to="Plaza"),
                 self._it("d", "drive", 0, 1000, km=5.47, p0=A, p1=B, frm="Motel", to="Coffs Harbour")]
        out = DL.join_parks(items)
        self.assertEqual([x["kind"] for x in out], ["charge", "drive"])
        d = out[1]
        self.assertEqual((d["t0"], d["t1"], d["km"], d["to"]), (0, 1060, 5.53, "Plaza"))
        self.assertEqual([p["id"] for p in d["parts"]], ["d", "m"])
        self.assertEqual(d["moves"], ["m"])
        self.assertFalse(d.get("short"))

    def test_a_short_move_right_before_a_drive_is_the_start_of_that_drive(self):
        A, B = [-30.2813, 153.1316], [-30.3098, 153.0977]
        items = [self._it("d", "drive", 1060, 2000, km=6.4, p0=A, p1=B, frm="Charger", to="Motel"), self._it("m", "drive", 1000, 1060, short=True, p0=A, p1=A, km=0.02),
                 self._it("c", "charge", 0, 1000, p0=A, p1=A)]
        out = DL.join_parks(items)
        self.assertEqual([x["kind"] for x in out], ["drive", "charge"])
        self.assertEqual((out[0]["t0"], out[0]["t1"]), (1000, 2000))
        self.assertEqual([p["id"] for p in out[0]["parts"]], ["m", "d"])

    def test_a_short_move_far_from_where_the_drive_ended_stays_its_own_row(self):
        A, B, C = [-30.3098, 153.0977], [-30.2813, 153.1316], [-30.2000, 153.2000]
        items = [self._it("m", "drive", 1010, 1070, short=True, p0=C, p1=C), self._it("d", "drive", 0, 1000, km=5.0, p0=A, p1=B)]
        self.assertEqual(len(DL.join_parks(items)), 2)

    def test_a_short_move_that_ends_elsewhere_is_not_absorbed(self):
        far = [-33.9, 151.2]
        items = [self._it("p", "parked", 1060, 5000, p0=far, p1=far), self._it("m", "drive", 1000, 1060, short=True, p1=self.H, p0=self.H)]
        self.assertEqual(len(DL.join_parks(items)), 2)

    def test_parks_at_different_places_stay_apart(self):
        far = [-33.9, 151.2]
        out = DL.join_parks([self._it("b", "parked", 1000, 2000, p0=far, p1=far), self._it("a", "parked", 100, 900)])
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0]["ongoing"])
        self.assertNotIn("ongoing", out[1])


class MissingTelemetry(unittest.TestCase):
    def rec(self, a, b, **kw):
        return cam(9, a, b, kind="recording", n=int((b - a) / 60), held=["J"], **kw)

    def test_a_recording_over_a_drive_and_a_park_is_a_button_on_the_drive_not_a_row(self):
        items = DL.merge([tess(1, 0, 300), tess(2, 1500, 1800)], [], [self.rec(0, 840)])
        self.assertEqual(by(items, "recording"), [])
        d = next(i for i in by(items, "drive") if i["id"] == "d1")
        self.assertEqual(d["tmiss"], dict(min=5, held=["J"]))
        self.assertNotIn("tmiss", next(i for i in by(items, "drive") if i["id"] == "d2"))
        self.assertTrue(all("tmiss" not in i for i in items if i["kind"] != "drive"))

    def test_a_recording_over_a_park_only_adds_nothing(self):
        items = DL.merge([tess(1, 0, 300), tess(2, 1500, 1800)], [], [self.rec(400, 900)])
        self.assertEqual(by(items, "recording"), [])
        self.assertTrue(all("tmiss" not in i for i in items))

    def test_a_recording_where_tessie_has_nothing_stays_a_row(self):
        items = DL.merge([tess(1, 0, 300)], [], [self.rec(5000, 5300)])
        self.assertEqual(len(by(items, "recording")), 1)


if __name__ == "__main__":
    unittest.main()
