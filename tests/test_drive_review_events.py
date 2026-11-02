#!/usr/bin/env python3
"""test_drive_review_events.py - what the drive review finds in the dashcam's per-second telemetry (lib/app_drive_review.py:
FSD switch-offs, accelerator presses, speed changes, the options), joining a Tessie drive with the dashcam seconds
(lib/drive_fuse.py), the solar shading residual (lib/shading_model.py), the drives-table text and the home test the footage
purge relies on (lib/drives/helpers.py), and the order the footage log reads the drives in (lib/app_footage.py).

Made-up drives, readings and places only. No video, no drive and no network is read.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_drive_review_events.py
"""
import calendar
import datetime
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import app_drive_review as RV  # noqa: E402
from lib import drive_fuse as DF  # noqa: E402
from lib import app_footage as AF  # noqa: E402
from lib.drives import helpers as H  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
try:
    import pvlib  # noqa: F401
    from lib import shading_model as SM
except ImportError:                      # pvlib is not installed in the sandbox: the two functions tested here do not use it
    sys.modules["pvlib"] = types.ModuleType("pvlib")
    try:
        from lib import shading_model as SM
    finally:
        del sys.modules["pvlib"]         # only the stand-in is taken away again


def rows(n, over=None, ap=1, v=100.0):
    """n one-second readings; over = {second: dict(field=value)} changes single seconds."""
    out = []
    for t in range(n):
        r = dict(t=t, la=-33.0 - t * 1e-4, lo=150.0, v=v, ap=ap, ac=0, st=0.0, blk=0, br=False, stamp="2026-11-01_10-00-00", i=t)
        r.update((over or {}).get(t, {}))
        out.append(r)
    return out


O = dict(RV.OPTS)


# ------------------------------------------------------------------------------------------------------------ review events
class InterventionTest(unittest.TestCase):
    def test_switch_off_with_brake(self):
        rs = rows(30, {**{t: dict(ap=0) for t in range(10, 15)}, 10: dict(ap=0, br=True)})
        ev = RV.interventions(rs, O)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual((e["t"], e["t1"], e["off_s"], e["fsd_before_s"]), (10, 15, 5, 10))
        self.assertEqual((e["from_mode"], e["to_mode"], e["flags"], e["at_stop"]), ("FSD", "Manual", ["brake"], False))

    def test_flags_and_stop(self):
        over = {t: dict(ap=2, v=1.0) for t in range(10, 30)}
        over[9] = dict(ac=20, st=-10.0)
        over[10] = dict(ap=2, v=1.0, st=10.0)
        e = RV.interventions(rows(30, over=over), O)[0]
        self.assertEqual((e["to_mode"], e["flags"], e["at_stop"]), ("Autosteer", ["accelerator", "steering"], True))
        self.assertIsNone(e["off_s"])           # FSD never came back
        self.assertEqual(e["t1"], e["t"])

    def test_not_a_switch_off(self):
        rs = rows(20)
        for r in rs[10:]:
            r["t"] += 10                         # a hole in the readings: not counted
            r["ap"] = 0
        self.assertEqual(RV.interventions(rs, O), [])
        self.assertEqual(RV.interventions(rows(20, over={10: dict(ap=None)}), O), [])   # an unknown mode is not a switch-off
        self.assertEqual(RV._mode(7), "mode 7")


class AcceleratorTest(unittest.TestCase):
    def test_presses_merge_across_short_gaps(self):
        over = {3: dict(ac=10), 4: dict(ac=30), 5: dict(ac=10), 8: dict(ac=6), 20: dict(ac=50), 21: dict(ap=0)}
        ev = RV.accel_presses(rows(30, over=over), O)
        self.assertEqual([(e["t"], e["t1"], e["dur_s"], e["peak"]) for e in ev], [(3, 8, 6, 30), (20, 20, 1, 50)])
        self.assertEqual(ev[0]["mean"], 14)
        self.assertEqual([e["ended_in_off"] for e in ev], [False, True])

    def test_light_touch_and_fsd_off_are_ignored(self):
        self.assertEqual(RV.accel_presses(rows(10, over={3: dict(ac=4)}), O), [])
        self.assertEqual(RV.accel_presses(rows(10, ap=0, over={3: dict(ac=40)}), O), [])


class SpeedChangeTest(unittest.TestCase):
    def test_one_lasting_change(self):
        rs = rows(70, over={t: dict(v=110.0) for t in range(30, 70)})
        ev = RV.speed_changes(rs, O)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual((e["v"], e["v1"], e["dur_s"], e["pressed"]), (100, 110, 5, False))
        self.assertTrue(25 <= e["t"] <= 35)

    def test_too_small_too_slow_or_fsd_off(self):
        self.assertEqual(RV.speed_changes(rows(70, over={t: dict(v=104.0) for t in range(30, 70)}), O), [])
        self.assertEqual(RV.speed_changes(rows(70, v=10.0, over={t: dict(v=18.0) for t in range(30, 70)}), O), [])
        self.assertEqual(RV.speed_changes(rows(70, ap=0, over={t: dict(v=110.0, ap=0) for t in range(30, 70)}), O), [])

    def test_two_changes_far_apart(self):
        over = {t: dict(v=110.0) for t in range(30, 70)}
        over.update({t: dict(v=80.0) for t in range(70, 110)})
        self.assertEqual([(e["v"], e["v1"]) for e in RV.speed_changes(rows(110, over=over), O)], [(100, 110), (110, 80)])

    def test_find_events_ids_in_time_order(self):
        over = {t: dict(v=110.0) for t in range(30, 80)}
        over.update({5: dict(ac=40), 70: dict(ap=0, v=110.0)})
        ev = RV.find_events(rows(80, over=over), O)
        self.assertEqual([e["id"] for e in ev], ["A1", "S1", "I1"])
        self.assertEqual(RV._end(ev[2]), ev[2]["t"])
        self.assertEqual(RV._end(ev[0]), ev[0]["t1"])


class ReviewHelpersTest(unittest.TestCase):
    def test_options(self):
        with mock.patch.object(RV.app_config, "get", return_value={"speed_min_kmh": 8, "unknown": 1}):
            o = RV._opts({"accel_min_pct": "12", "merge_s": "x", "nope": 3, "traffic": None})
        self.assertEqual((o["speed_min_kmh"], o["accel_min_pct"], o["merge_s"], o["traffic"]), (8, 12, 3, True))
        self.assertNotIn("unknown", o)
        self.assertNotIn("nope", o)

    def test_rows_side_and_clip(self):
        r = RV.to_rows([(5, -33.0, 150.0, 80.0, 1, 0, 1.5, 0, False, "2026-11-01_10-00-00", 5)])[0]
        self.assertEqual((r["t"], r["v"], r["st"], r["stamp"], r["i"]), (5, 80.0, 1.5, "2026-11-01_10-00-00", 5))
        self.assertEqual([RV._side(x, 100) for x in (10, 50, 90)], ["L", "C", "R"])
        self.assertEqual(RV._clip_of(rows(10), 6.4), dict(stamp="2026-11-01_10-00-00", s=6))
        self.assertIsNone(RV._clip_of([], 1))


# ------------------------------------------------------------------------------------------------------------ drive fuse
class DriveFuseTest(unittest.TestCase):
    def test_clock_and_distance(self):
        self.assertEqual(DF.secs("2026-11-01_10-00-00"), calendar.timegm((2026, 11, 1, 10, 0, 0)))
        self.assertEqual(DF.utc(36000 + 100, off=36000), 100)
        self.assertEqual(DF.local(100, off=37800), 37900)
        self.assertAlmostEqual(DF._km(-33.0, 150.0, -34.0, 150.0), 111.195, places=2)

    def test_interp_and_lerp(self):
        self.assertIsNone(DF._interp([], [], 5))
        self.assertEqual([DF._interp([0, 10], [0.0, 100.0], t) for t in (-5, 2.5, 15)], [0.0, 25.0, 100.0])
        rs = [(0, None, None, None, 10.0), (10, None, None, None, None), (20, None, None, None, 30.0)]
        ts = [r[0] for r in rs]
        self.assertEqual(DF._lerp(ts, rs, 4, 5), 10.0)       # one side has no value: the other side's
        self.assertEqual(DF._lerp(ts, rs, 4, -1), 10.0)
        self.assertEqual(DF._lerp(ts, rs, 4, 99), 30.0)
        self.assertIsNone(DF._lerp([], [], 4, 1))

    def tessie(self):
        return [dict(t=t, u=t - 36000, la=-33.0 - t * 1e-5, lo=150.0, v=60.0, el=float(t) / 10) for t in range(0, 301, 30)]

    def cam(self, a=60, b=200):
        return [(t, -33.0 - t * 1e-5, 150.0, 61.0, 1, 0, 0.0, 0, False, "2026-11-01_10-00-00", t % 60) for t in range(a, b + 1)]

    def test_camera_seconds_replace_nearby_tessie_points(self):
        out = DF.fuse(self.tessie(), self.cam(), 0, 300)
        pts, info = out["points"], out["info"]
        tessie_kept = [t for t, s in zip(pts["t"], pts["s"]) if s == 0]
        self.assertEqual(tessie_kept, [0, 30, 240, 270, 300])     # 60..210 lie within 20 s of a camera second
        self.assertEqual(pts["t"], sorted(pts["t"]))
        self.assertEqual(pts["el"][pts["t"].index(75)], 7.5)       # the camera has no elevation: Tessie's, on a line
        self.assertEqual((info["start_gap_s"], info["end_gap_s"], info["tessie_every_s"], info["cam_n"]), (60, 100, 30, 141))
        self.assertEqual(info["cam_pct"], 47)
        self.assertFalse(info["thinned"])

    def test_no_camera(self):
        out = DF.fuse(self.tessie(), [], 0, 300)
        self.assertEqual(out["points"]["s"], [0] * 11)
        self.assertIsNone(out["info"]["start_gap_s"])

    def test_thinning_keeps_the_last_point(self):
        with mock.patch.object(DF, "MAX_POINTS", 50):
            out = DF.fuse(self.tessie(), self.cam(), 0, 300)
        self.assertTrue(out["info"]["thinned"])
        self.assertLessEqual(len(out["points"]["t"]), 51)
        self.assertEqual(out["points"]["t"][-1], 300)

    def test_clock_offset_checks_the_camera_against_the_route(self):
        fake_cc = types.SimpleNamespace(car_offset=lambda n: 36000.0, naive=lambda s: 0, refresh=lambda *a, **k: False)
        tp = self.tessie()
        ts, las, los = [p["t"] for p in tp], [p["la"] for p in tp], [p["lo"] for p in tp]
        with mock.patch.object(DF, "CC", fake_cc), mock.patch.object(DF, "camera_points", return_value=self.cam()):
            self.assertEqual(DF.clock_offset(ts, las, los, None, 0, 300)[0], "UTC+10")
        far = [(c[0], c[1] + 0.5) + c[2:] for c in self.cam()]       # 55 km off the route: another drive
        with mock.patch.object(DF, "CC", fake_cc), mock.patch.object(DF, "camera_points", return_value=far):
            self.assertEqual(DF.clock_offset(ts, las, los, None, 0, 300), (None, []))
        with mock.patch.object(DF, "CC", fake_cc), mock.patch.object(DF, "camera_points", return_value=self.cam(60, 65)):
            self.assertEqual(DF.clock_offset(ts, las, los, None, 0, 300), (None, []))   # too few seconds to tell

    def test_enrich(self):
        page = [[t, -33.0, 150.0, 60.0, 10.0 + t / 30, None, 80.0] for t in range(0, 301, 30)]
        with mock.patch.object(DF, "clock_offset", return_value=("UTC+10", self.cam(60, 120))):
            out = DF.enrich(page, None)
        self.assertEqual(out["info"]["clock"], "UTC+10")
        r75 = next(r for r in out["rows"] if r[0] == 75)
        self.assertEqual(len(r75), 16)
        self.assertEqual((r75[3], r75[4], r75[6], r75[11]), (61.0, 12.5, 80.0, 1))     # power on a line, camera speed and FSD mode
        self.assertEqual(next(r for r in out["rows"] if r[0] == 0)[11:], [None] * 5)
        with mock.patch.object(DF, "clock_offset", return_value=(None, [])):
            self.assertIsNone(DF.enrich(page, None))
        self.assertIsNone(DF.enrich([], None))
        self.assertIsNone(DF.enrich([[0, None, None]], None))


# ------------------------------------------------------------------------------------------------------------ shading model
class ShadingModelTest(unittest.TestCase):
    def test_panel_groups(self):
        cfg = {"panel_groups": [{"tilt_deg": 20, "azimuth_deg_from_true_north": -30, "panel_count": 10},
                                {"tilt_deg": 15, "azimuth_deg_from_true_north": 45, "panel_count": 4},
                                {"tilt_deg": None, "panel_count": 3}, {"tilt_deg": 10, "panel_count": 0},
                                {"tilt_deg": 10, "azimuth_deg_from_true_north": None, "panel_count": 2}]}
        self.assertEqual(SM.panel_groups_from_config(cfg), [(20, 330, 10), (15, 45, 4), (10, 0, 2)])

    def day_frame(self, day, afternoon_actual, morning_noise=0.0):
        """One made-up day at 15 minutes: modelled sun 100 W/m2 all day, a west-low sun from 4pm; the panels give 0.01 kWh per
        100 W/m2 in the morning."""
        idx = pd.date_range(f"{day} 06:00", f"{day} 19:00", freq="15min", tz=SM.TZ, inclusive="left")
        az = np.where(idx.hour >= 16, 260.0, 90.0)
        el = np.where(idx.hour >= 16, 20.0, 45.0)
        cs = pd.DataFrame({"modeled_poa": 100.0, "sun_azimuth": az, "sun_elevation": el}, index=idx)
        actual = pd.Series(np.where(idx.hour >= 16, afternoon_actual, 0.01), index=idx)
        if morning_noise:
            actual[(idx.hour >= 10) & (idx.hour < 13)] = [0.01 + morning_noise * (i % 2) for i in range(12)]
        return cs, actual

    def test_residual_of_a_clear_morning(self):
        cs, actual = self.day_frame("2026-01-10", afternoon_actual=0.005)
        r = SM.compute_daily_residuals(cs, actual)
        self.assertEqual(len(r), 1)
        row = r.iloc[0]
        self.assertAlmostEqual(row["calibration"], 0.0001)
        self.assertEqual(row["n_aft_intervals"], 12)
        self.assertAlmostEqual(row["aft_modeled_kwh"], 0.12)
        self.assertAlmostEqual(row["aft_actual_kwh"], 0.06)
        self.assertAlmostEqual(row["residual_pct"], 0.5)

    def test_a_patchy_morning_is_not_used(self):
        cs, actual = self.day_frame("2026-01-10", afternoon_actual=0.005, morning_noise=0.01)
        self.assertEqual(len(SM.compute_daily_residuals(cs, actual)), 0)


# ------------------------------------------------------------------------------------------------------------ drive text, home
class DriveHelpersTest(unittest.TestCase):
    def test_durations(self):
        self.assertEqual([H.format_duration_short(m) for m in (0, None, 5, 60, 75, 125)], ["-", "-", "5m", "1h", "1h 15m", "2h 5m"])

    def test_efficiency(self):
        self.assertEqual(H.format_drive_efficiency(dict(dist_km=50, wh_km=165)), "16.5 kWh/100km")
        self.assertEqual(H.format_drive_efficiency(dict(dist_km=50, wh_km="-", kwh_used=8)), "16.0 kWh/100km")
        self.assertEqual(H.format_drive_efficiency(dict(dist_km=0.05, wh_km=165)), "-")       # too short to mean anything
        self.assertEqual(H.format_drive_efficiency(dict(dist_km=1, wh_km=1500)), "-")          # out of bounds
        self.assertEqual(H.format_drive_efficiency(dict(dist_km=1, kwh_used=-1)), "-")
        self.assertEqual(H.format_drive_efficiency(dict(dist_km=None, kwh_used=5)), "-")
        self.assertEqual(H.format_drive_efficiency(dict(dist_km="x", wh_km="y")), "-")

    def test_event_text(self):
        self.assertEqual(H.clean_event_reason("user_interaction_dashcam_launcher_action_tapped"), "action tapped")
        self.assertEqual(H.clean_event_reason("sentry_aware_object_detection"), "object detection")
        self.assertEqual(H.clean_event_reason(""), "event")
        self.assertEqual(H.format_footage_tag({"Sentry", "Saved", "Mystery"}), "📹 Saved, 🛡️ Sentry")
        self.assertEqual(H.format_footage_tag(set()), "No local footage")
        self.assertEqual(H.format_footage_tag({"Mystery"}), "No local footage")

    def test_home(self):
        places = {"Our Place": {"type": "home", "nickname": "Base", "tesla_metadata": {"short_name": "Our_Place"},
                                "keywords": ["1 Example Street"]},
                  "Home": {"type": "work"}}
        self.assertTrue(H.is_home_place("🏠 Home"))
        self.assertTrue(H.is_home_place("HOME"))
        for s in ("Home Depot", "At Home Furniture", "", None, "Homeward"):
            with self.subTest(s=s):
                self.assertFalse(H.is_home_place(s))
        self.assertTrue(H.is_home_place("Our Place", places))
        self.assertTrue(H.is_home_place("base", places))
        self.assertTrue(H.is_home_place("our_place", places))
        self.assertTrue(H.is_home_place("1 example street, testville nsw 2999", places))      # an old address label
        self.assertFalse(H.is_home_place("Work", places))


# ------------------------------------------------------------------------------------------------------------ footage log job
class FootageJobTest(unittest.TestCase):
    def setUp(self):
        self.scanned = []
        test = self

        class FakeLedger:
            def __init__(self, data_dir):
                pass

            def scan(self, role, path):
                if role == "X" and test.fail:
                    raise OSError("the drive went away")
                test.scanned.append(role)
                return dict(note="note %s" % role if role == "J" else "")
        self.fail = False
        p1 = mock.patch.object(AF.TLG, "Ledger", FakeLedger)
        p2 = mock.patch.object(AF.ST, "Stills", mock.MagicMock())
        p3 = mock.patch.object(AF.app_config, "get", side_effect=lambda k, d=None: d)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        self.F = AF.Footage("/tmp/nodata")

    def test_drives_are_read_in_a_fixed_order(self):
        self.F.job = dict(self.F.job, done=0)
        self.F._run_log({"X": "/x", "U": "/u", "?": "/q", "A": "/a", "J": "/j"})
        self.assertEqual(self.scanned, ["A", "J", "U", "?", "X"])
        self.assertEqual((self.F.job["done"], self.F.job["note"], self.F.job["error"]), (5, "note J", ""))

    def test_a_drive_pulled_out_is_reported(self):
        self.fail = True
        self.F._run_log({"X": "/x", "A": "/a"})
        self.assertEqual(self.scanned, ["A"])
        self.assertEqual(self.F.job["error"], "OSError: the drive went away")

    def test_stop(self):
        self.F.stop()
        self.F._run_log({"A": "/a"})
        self.assertEqual(self.scanned, [])


if __name__ == "__main__":
    unittest.main()
