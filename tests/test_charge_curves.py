#!/usr/bin/env python3
"""test_charge_curves.py - the charging-curve numbers (lib/charge_curves.py): reading a deep-dive telemetry CSV, the station
from its file name, the 20-80% time (measured, or estimated from earlier sessions on the same hardware), the dashed history
line, the per-moment solar/grid split of a home charge, the chart titles; and the charge deep-dive file names and day folders
(lib/charge_paths.py).

Made-up sessions, stations and files only (temporary folders). The PV database is replaced by fakes; no chart is drawn.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_charge_curves.py
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import charge_curves as CC  # noqa: E402
from lib import charge_paths as CP  # noqa: E402

REGISTRY = {
    "Example Plaza": {"tesla_metadata": {"short_name": "Example_Plaza_NSW"}, "hardware": {"tier": "V3", "max_power_kw": 250}},
    "Sample Town": {"tesla_metadata": {"short_name": "Sample_Town_VIC"}, "hardware": {"tier": "V3", "max_power_kw": 250}},
    "Other Place": {"tesla_metadata": {"short_name": "Other_Place_QLD"}, "hardware": {"tier": "V2", "max_power_kw": 150}},
}


def session(path, slug, lo, hi, min_per_pct=1.0, power=lambda soc: 120.0):
    """A made-up session charging from lo% to hi% at a steady pace, one point per percent."""
    pts = []
    for soc in range(lo, hi + 1):
        m = (soc - lo) * min_per_pct
        pts.append(dict(t="2026-08-01 10:00:00", elapsed_min=m, soc=float(soc), power_kw=power(soc),
                        voltage_v=350.0 + soc, current_a=300.0 - soc, temp_c=30.0))
    return dict(path=path, slug=slug, points=pts)


class FileNameTest(unittest.TestCase):
    def test_slug(self):
        f = CC.slug_from_deepdive_filename
        self.assertEqual(f("/x/charge_deepdive_2026-08-14_2109_Tesla_Example_Plaza_NSW.csv"), "Tesla_Example_Plaza_NSW")
        self.assertEqual(f("charge_deepdive_2026-06-20_1327_00_Evnex_Home.csv"), "Evnex_Home")      # the _NN counter is not part of it
        self.assertEqual(f("charge_deepdive_2026-06-20_1327_AEDT_03_Evnex_Home.csv"), "Evnex_Home")
        self.assertIsNone(f("drive_deepdive_2026-06-20_1327_Example.csv"))
        self.assertIsNone(f(None or ""))

    def test_hardware_match_strips_the_network(self):
        self.assertEqual(CC.match_hardware("Tesla_Example_Plaza_NSW", REGISTRY), ("Example Plaza", {"tier": "V3", "max_power_kw": 250}))
        self.assertEqual(CC.match_hardware("Example_Plaza_NSW", REGISTRY)[0], "Example Plaza")
        self.assertEqual(CC.match_hardware("Tesla_Nowhere", REGISTRY), (None, None))
        self.assertEqual(CC.match_hardware(None, REGISTRY), (None, None))


class TelemetryCsvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_curves_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text):
        p = os.path.join(self.tmp, "charge_deepdive_2026-11-01_1000_Tesla_Example_Plaza_NSW.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_points(self):
        p = self.write("Timestamp (AEDT),Battery Level (%),Charger Power (kW),Pack Voltage (V),Pack Current (A),"
                       "Min Battery Module Temp (°C),Max Battery Module Temp (°C)\n"
                       "2026-11-01 10:00:00,20,100,360,280,20,30\n"
                       "bad time,21,100,360,280,20,30\n"
                       "2026-11-01 10:01:30,,100,360,280,20,30\n"           # no SoC: skipped
                       "2026-11-01 10:03:00,22,,361,,25,\n")
        pts = CC.parse_telemetry_csv(p)
        self.assertEqual(len(pts), 2)
        self.assertEqual((pts[0]["elapsed_min"], pts[0]["temp_c"], pts[0]["power_kw"]), (0.0, 25.0, 100.0))
        self.assertEqual((pts[1]["elapsed_min"], pts[1]["temp_c"], pts[1]["power_kw"], pts[1]["current_a"]), (3.0, None, None, None))

    def test_not_telemetry(self):
        self.assertIsNone(CC.parse_telemetry_csv(self.write("Started At (AEST),Energy Added (kWh)\n2026-11-01 10:00:00,5\n")))
        self.assertIsNone(CC.parse_telemetry_csv(os.path.join(self.tmp, "missing.csv")))


class PacePerPercentTest(unittest.TestCase):
    def test_minutes_per_percent(self):
        pts = [dict(soc=50.0, elapsed_min=0.0), dict(soc=51.0, elapsed_min=2.0), dict(soc=53.0, elapsed_min=6.0),
               dict(soc=54.0, elapsed_min=9.0), dict(soc=None, elapsed_min=10.0)]
        # 50 is left out (the session starts part-way into it); 52 was skipped and is filled in on a straight line
        self.assertEqual(CC._minutes_per_percent(pts), {51: 2.0, 52: 2.0, 53: 3.0})
        self.assertEqual(CC._minutes_per_percent([dict(soc=50.0, elapsed_min=0.0)]), {})

    def test_interp(self):
        self.assertEqual(CC._interp([0, 10], [0, 100], 2.5), 25.0)
        self.assertIsNone(CC._interp([0, 10], [0, 100], 11))
        self.assertIsNone(CC._interp([0], [0], 0))
        self.assertEqual(CC._interp([0, 0, 10], [5, 7, 9], 0), 5)


class WindowEstimateTest(unittest.TestCase):
    def test_measured(self):
        cur = session("a", "Tesla_Example_Plaza_NSW", 10, 90)
        r = CC.estimate_window_minutes([cur], cur, REGISTRY)
        self.assertEqual((r["minutes"], r["measured"]), (60.0, True))
        self.assertEqual(CC.window_line(r), "20% → 80%: 60.0 min (measured)")

    def test_home_is_not_estimated(self):
        cur = session("a", "Evnex_Home", 50, 60)
        r = CC.estimate_window_minutes([cur], cur, REGISTRY)
        self.assertIsNone(r["minutes"])
        self.assertIn("home", r["text"])

    def test_no_history(self):
        cur = session("a", "Tesla_Example_Plaza_NSW", 50, 90)
        r = CC.estimate_window_minutes([cur], cur, REGISTRY)
        self.assertIsNone(r["minutes"])
        self.assertIn("cannot be estimated", CC.window_line(r))

    def test_estimate_from_the_same_site_scaled_to_this_session(self):
        hist = session("h", "Tesla_Example_Plaza_NSW", 10, 90, min_per_pct=1.0)
        cur = session("c", "Tesla_Example_Plaza_NSW", 50, 90, min_per_pct=1.5)    # this session charged 1.5x slower
        r = CC.estimate_window_minutes([hist, cur], cur, REGISTRY)
        self.assertFalse(r["measured"])
        # 51..79 measured at 1.5 min each (29 steps); 20..50 from history (31 steps) at 1.0 x 1.5
        self.assertAlmostEqual(r["minutes"], 29 * 1.5 + 31 * 1.5)
        self.assertEqual((r["info"]["n_hist"], r["info"]["factor"]), (31, 1.5))
        self.assertIn("1 earlier session at this site", r["text"])

    def test_scale_is_capped(self):
        hist = session("h", "Tesla_Example_Plaza_NSW", 10, 90, min_per_pct=1.0)
        cur = session("c", "Tesla_Example_Plaza_NSW", 50, 90, min_per_pct=5.0)
        self.assertEqual(CC.estimate_window_minutes([hist, cur], cur, REGISTRY)["info"]["factor"], 1.6)

    def test_same_tier_elsewhere_fills_in(self):
        other = session("o", "Tesla_Sample_Town_VIC", 10, 90)           # same V3 tier, another site
        slower = session("v2", "Tesla_Other_Place_QLD", 10, 90, min_per_pct=9.0, power=lambda s: 30.0)   # V2 and slow AC-like: not used
        cur = session("c", "Tesla_Example_Plaza_NSW", 50, 90)
        r = CC.estimate_window_minutes([other, slower, cur], cur, REGISTRY)
        self.assertAlmostEqual(r["minutes"], 60.0)
        self.assertIn("on V3 chargers elsewhere", r["text"])

    def test_a_session_that_saw_little_of_the_window_is_flagged(self):
        hist = session("h", "Tesla_Example_Plaza_NSW", 10, 90)
        cur = session("c", "Tesla_Example_Plaza_NSW", 75, 90)
        r = CC.estimate_window_minutes([hist, cur], cur, REGISTRY)
        self.assertIsNotNone(r["minutes"])
        self.assertIn("indicative only", r["text"])
        self.assertTrue(CC.window_line(r).startswith("20% → 80%: ~"))


class HistoryLineTest(unittest.TestCase):
    def test_home_and_no_history(self):
        home = session("a", "Evnex_Home", 50, 60)
        self.assertIsNone(CC.historical_extension([home], home, REGISTRY))
        cur = session("a", "Tesla_Example_Plaza_NSW", 50, 60)
        self.assertIsNone(CC.historical_extension([cur], cur, REGISTRY))
        self.assertIn("no charging history", CC._history_caption(None))

    def test_before_and_after_from_the_same_site(self):
        hist = session("h", "Tesla_Example_Plaza_NSW", 10, 90, power=lambda s: 200.0 - 2 * s)
        cur = session("c", "Tesla_Example_Plaza_NSW", 50, 89)
        r = CC.historical_extension([hist, cur], cur, REGISTRY)
        before = r["power_kw"]["before"]
        self.assertEqual((before[0][0], before[-1][0]), (10, 50))      # up to this session's own lowest SoC
        self.assertAlmostEqual(dict(before)[30], 140.0)
        self.assertEqual([x for x, _ in r["power_kw"]["after"]], [89, 90])
        self.assertFalse(r["used_tier_fallback"])
        self.assertEqual(CC._history_caption(r), "dashed = estimated from 1 other session at this site")

    def test_idle_samples_are_left_out(self):
        hist = session("h", "Tesla_Example_Plaza_NSW", 10, 60, power=lambda s: 0.5 if s % 2 else 100.0)
        cur = session("c", "Tesla_Example_Plaza_NSW", 50, 60)
        r = CC.historical_extension([hist, cur], cur, REGISTRY)
        self.assertTrue(all(v == 100.0 for _, v in r["power_kw"]["before"]))

    def test_tier_fallback_caption(self):
        other = session("o", "Tesla_Sample_Town_VIC", 10, 90)
        cur = session("c", "Tesla_Example_Plaza_NSW", 50, 60)
        r = CC.historical_extension([other, cur], cur, REGISTRY)
        self.assertTrue(r["used_tier_fallback"])
        self.assertIn("1 other V3 session (no history at this exact site yet)", CC._history_caption(r))


class TitleTest(unittest.TestCase):
    def test_ordinal_dates(self):
        cases = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th", 12: "12th", 13: "13th", 21: "21st", 22: "22nd", 23: "23rd", 31: "31st"}
        for d, want in cases.items():
            with self.subTest(day=d):
                self.assertEqual(CC._ordinal_date(datetime(2026, 8, d)), f"{want} Aug 2026")

    def test_build_title(self):
        s = dict(slug="Tesla_Example_Plaza_NSW", points=[dict(t="2026-08-01 10:00:00")])
        self.assertEqual(CC.build_title(s, "Example Plaza", {"tier": "V3", "max_power_kw": 250}), "1st Aug 2026 - Example Plaza (V3 250kW)")
        self.assertEqual(CC.build_title(s, None, None), "1st Aug 2026 - Tesla Example Plaza NSW")
        self.assertEqual(CC.build_title(dict(slug="Evnex_Home", points=[]), None, None), "Home (Evnex)")
        self.assertEqual(CC.build_title(dict(slug="X", points=[dict(t="2026-08-01")]), "X", None), "2026-08-01 - X")


class HomeSolarSplitTest(unittest.TestCase):
    def patch_pv(self, intervals, whole_fraction, key="k"):
        for name, value in (("_PV_CONN", object()), ("_PV_KEY", key), ("_PV_ACCOUNT_ID", "a"), ("_PV_PROVIDER", None),
                            ("pv_interval_series", mock.MagicMock(return_value=intervals)),
                            ("pv_window_split", mock.MagicMock(return_value={"solar_fraction": whole_fraction}))):
            p = mock.patch.object(CC, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)

    POINTS = [dict(t="2026-11-01 10:00:00", power_kw=7.0), dict(t="2026-11-01 10:20:00", power_kw=4.0),
              dict(t="bad", power_kw=2.0), dict(t="2026-11-01 10:40:00", power_kw=None)]
    IVALS = [dict(start=datetime(2026, 11, 1, 10, 0), end=datetime(2026, 11, 1, 10, 15), solar_fraction=0.5),
             dict(start=datetime(2026, 11, 1, 10, 15), end=datetime(2026, 11, 1, 10, 30), solar_fraction=1.7)]

    def test_per_interval_fraction_then_the_whole_session(self):
        self.patch_pv(self.IVALS, 0.25)
        got = CC.compute_home_solar_grid_split(self.POINTS)
        self.assertEqual(got, [(3.5, 3.5), (4.0, 0.0), (0.0, 2.0), (0.0, 0.0)])   # 1.7 is held to 1; 10:40 uses 0.25 of 0 kW

    def test_whole_session_fraction_only(self):
        self.patch_pv([], 0.25)
        self.assertEqual(CC.compute_home_solar_grid_split(self.POINTS[:2]), [(1.75, 5.25), (1.0, 3.0)])

    def test_no_pv_data(self):
        self.patch_pv([], None)
        self.assertIsNone(CC.compute_home_solar_grid_split(self.POINTS[:2]))
        self.patch_pv(self.IVALS, 0.25, key=None)
        self.assertIsNone(CC.compute_home_solar_grid_split(self.POINTS[:2]))
        self.assertIsNone(CC.compute_home_solar_grid_split([]))


class ChargePathsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test_deepdive_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def touch(self, *parts):
        p = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
        return p

    def test_stamps(self):
        self.assertEqual(CP.stamp_of("charge_deepdive_2026-06-12_1702_AEST_00_Evnex_Home.csv"), "2026-06-12_17-02_AEST")
        self.assertEqual(CP.stamp_of("/a/charge_deepdive_2026-06-12_1702_Tesla_X.csv"), "2026-06-12_17-02")
        self.assertIsNone(CP.stamp_of("drive_deepdive_2026-06-12_1702.csv"))
        self.assertEqual(CP.day_of("2026-06-12_17-02_AEST"), "2026-06-12")
        self.assertEqual(CP.folder_of_name("/r", "charge_deepdive_2026-06-12_1702_X.csv"), os.path.join("/r", "2026-06-12", "2026-06-12_17-02"))
        self.assertIsNone(CP.folder_of_name("/r", "notes.txt"))

    def test_both_layouts_day_folder_wins(self):
        name = "charge_deepdive_2026-06-12_1702_X.csv"
        flat = self.touch(name)
        self.assertEqual(CP.find(self.root, name), flat)
        nested = self.touch("2026-06-12", "2026-06-12_17-02", name)
        self.assertEqual(CP.find(self.root, name), nested)
        self.touch("charge_deepdive_2026-06-11_0900_Y.csv")
        self.touch("2026-06-12", "2026-06-12_17-02", "notes.txt")
        self.assertEqual(CP.names(self.root), ["charge_deepdive_2026-06-11_0900_Y.csv", name])
        self.assertEqual(dict(CP.list_files(self.root))[name], nested)
        self.assertEqual(CP.glob_files(self.root, "*_Y.csv"), [os.path.join(self.root, "charge_deepdive_2026-06-11_0900_Y.csv")])
        self.assertEqual(CP.list_files(os.path.join(self.root, "missing")), [])

    def test_unique_path_counts_past_files_in_either_layout(self):
        self.touch("charge_deepdive_2026-06-12_1702_AEST_00_Home.csv")
        self.touch("2026-06-12", "2026-06-12_17-02_AEST", "charge_deepdive_2026-06-12_1702_AEST_01_Home.csv")
        p = CP.unique_path(self.root, "2026-06-12_1702_AEST", suffix="_Home", ext=".csv")
        self.assertEqual(p, os.path.join(self.root, "2026-06-12", "2026-06-12_17-02_AEST", "charge_deepdive_2026-06-12_1702_AEST_02_Home.csv"))
        self.assertFalse(os.path.exists(os.path.dirname(p)) and os.path.exists(p))

    def test_root_of(self):
        deep = os.path.join(self.root, "deepdive")
        self.assertEqual(CP.root_of(os.path.join(deep, "2026-06-12", "2026-06-12_17-02", "f.csv")), deep)
        self.assertEqual(CP.root_of(os.path.join(deep, "f.csv")), deep)
        self.assertEqual(CP.root_of(os.path.join(self.root, "a", "f.csv")), os.path.join(self.root, "a"))


if __name__ == "__main__":
    unittest.main()
