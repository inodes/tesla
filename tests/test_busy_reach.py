#!/usr/bin/env python3
"""test_busy_reach.py - charger busy times by local hour (lib/tesla_busy_times.py: Tesla's profile is kept by UTC hour with
the station's offset), the range map's picture and sums (lib/app_reach.py: the PNG, the colour tables, the car's range, the
charge above the reserve), and ABRP's arrival charge, badge and route link (lib/abrp_common.py).

Made-up profiles, stations and car figures only. No network and no road network: the road graph is a fake.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_busy_reach.py
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
import urllib.parse
import zlib
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import tesla_busy_times as BT  # noqa: E402
from lib import app_reach as AR  # noqa: E402
from lib import abrp_common as AB  # noqa: E402


def flat_profile(value=0.1, offset_s=36000):
    return {"utc_offset_s": offset_s, "days": {d: [value] * 24 for d in BT.DAYS_SUN_FIRST}}


# ------------------------------------------------------------------------------------------------------------ busy times
class BusyProfileTest(unittest.TestCase):
    def test_extract(self):
        payload = {"props": {"pageProps": {"formattedData": {"chargerDetails": {"availabilityProfile": {
            "utcOffset": "36000s", "createdTimestamp": "2026-10-01",
            "availabilityProfile": {d: {"congestionValue": [0.12345] * 24} for d in BT.DAYS_SUN_FIRST}}}}}}}
        got = BT.extract_busy_profile(payload)
        self.assertEqual(got["utc_offset_s"], 36000)
        self.assertEqual(got["profile_date"], "2026-10-01")
        self.assertEqual(got["days"]["monday"][0], 0.123)
        payload["props"]["pageProps"]["formattedData"]["chargerDetails"]["availabilityProfile"]["availabilityProfile"]["monday"]["congestionValue"] = [0.1]
        self.assertIsNone(BT.extract_busy_profile(payload))       # not 24 hours
        self.assertIsNone(BT.extract_busy_profile(None))
        self.assertIsNone(BT.extract_busy_profile({"props": {}}))

    def test_utc_hours_become_local_hours_across_midnight(self):
        p = flat_profile()
        p["days"]["monday"][3] = 0.9          # 03:00 UTC Monday = 1pm Monday at UTC+10
        p["days"]["sunday"][20] = 0.8         # 20:00 UTC Sunday = 6am Monday at UTC+10
        mon = BT.local_hourly(p, 0)
        self.assertEqual((mon[13], mon[6], mon[12]), (0.9, 0.8, 0.1))
        self.assertEqual(BT.local_hourly(p, 1)[13], 0.1)
        sun = BT.local_hourly(p, 6)            # Sunday 6am local is Saturday 20:00 UTC
        self.assertEqual(sun[6], 0.1)
        west = dict(p, utc_offset_s=-3600 * 5)  # a station behind UTC: local 22:00 Sunday = 03:00 UTC Monday
        self.assertEqual(BT.local_hourly(west, 6)[22], 0.9)

    def test_averages(self):
        p = flat_profile(0.0)
        p["days"]["saturday"] = [0.6] * 24
        p["days"]["sunday"] = [0.4] * 24
        self.assertAlmostEqual(BT.local_hourly(dict(p, utc_offset_s=0), BT.WEEKENDS)[12], 0.5)
        self.assertAlmostEqual(BT.local_hourly(dict(p, utc_offset_s=0))[12], 1.0 / 7)

    def test_summary_quiet_and_busy_window(self):
        p = flat_profile(0.2, offset_s=0)
        s = BT.busy_summary(p)
        self.assertEqual(s["level"], "most")
        self.assertIsNone(s["some_window"])
        self.assertTrue(s["text"].startswith("Most chargers available all day (busiest 12am, 20% in use)"))
        for h, v in ((11, 0.5), (12, 0.6), (13, 0.7), (14, 0.55)):
            for d in BT.DAYS_SUN_FIRST:
                p["days"][d][h] = v
        s = BT.busy_summary(p)
        self.assertEqual((s["level"], s["peak_hour"], s["peak_pct"], s["some_window"]), ("some", 13, 70, (11, 15)))
        self.assertEqual(s["text"], "Some chargers available 11am–3pm, most at other times (busiest 1pm, 70% in use)")

    def test_busy_window_across_midnight(self):
        p = flat_profile(0.1, offset_s=0)
        for d in BT.DAYS_SUN_FIRST:
            p["days"][d][23] = 0.8
            p["days"][d][0] = 0.6
        self.assertEqual(BT.busy_summary(p)["some_window"], (23, 1))

    @unittest.expectedFailure
    def test_busy_all_day(self):
        """BUG (found by this test): when every hour is at or above SOME_AVAILABLE_FROM the busy window wraps right round,
        and the text reads 'Some chargers available 1am–1am, most at other times' - there are no other times."""
        s = BT.busy_summary(flat_profile(0.7, offset_s=0))
        self.assertNotIn("most at other times", s["text"])

    def test_sparkline_and_labels(self):
        vals = [0.0] * 24
        vals[4] = 1.0
        line = BT.sparkline(vals)
        self.assertEqual(len(line), 24)
        self.assertEqual(line[0], BT.SPARK[-1])            # the line starts at 4am
        self.assertEqual(BT.sparkline([2.0] * 24)[0], BT.SPARK[-1])
        self.assertEqual([BT._hour_label(h) for h in (0, 1, 12, 13, 24)], ["12am", "1am", "12pm", "1pm", "12am"])
        self.assertEqual((BT.level(0.49), BT.level(0.5)), ("most", "some"))

    def test_save_and_find_by_station_id(self):
        tmp = tempfile.mkdtemp(prefix="test_busy_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "chargers", BT.BUSY_FILE_NAME)
        self.assertFalse(BT.save_busy_profile("", "X", flat_profile(), path))
        self.assertTrue(BT.save_busy_profile(123, "Example Plaza", flat_profile(), path))
        self.assertTrue(BT.save_busy_profile("456", "Sample Town", flat_profile(0.3), path))
        data = BT.load_busy_times(path)
        self.assertEqual(sorted(data), ["123", "456"])
        self.assertEqual(data["123"]["station_name"], "Example Plaza")
        with open(path, "w") as f:
            f.write("{broken")
        self.assertFalse(BT.save_busy_profile("789", "Y", flat_profile(), path))    # an unreadable file is left alone
        with open(path) as f:
            self.assertEqual(f.read(), "{broken")
        with mock.patch.object(BT, "_CACHE", {"456": {"station_name": "Sample Town"}}):
            st = types.SimpleNamespace(source_id=None, metadata={"findus_id": None, "secondary_source_ids": [456]})
            self.assertEqual(BT.station_busy_profile(st)["station_name"], "Sample Town")
            self.assertIsNone(BT.station_busy_profile(types.SimpleNamespace(source_id="1", metadata=None)))


# ------------------------------------------------------------------------------------------------------------ range map
def read_png(data):
    """(width, height, rows of RGBA bytes) from png_bytes() output - enough to check it is a real PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, chunks = 8, {}
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        t = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + n]
        crc = struct.unpack(">I", data[i + 8 + n:i + 12 + n])[0]
        assert crc == zlib.crc32(t + body) & 0xFFFFFFFF
        chunks[t] = body
        i += 12 + n
    w, h = struct.unpack(">II", chunks[b"IHDR"][:8])
    raw = zlib.decompress(chunks[b"IDAT"])
    return w, h, [raw[r * (w * 4 + 1) + 1:(r + 1) * (w * 4 + 1)] for r in range(h)]


CONFIG = {"reach.reserve_pct": 10, "reach.image_px": 200, "reach.shade_km": 8, "reach.gaps_shade_km": 8}


class FakeGraph:
    lat = np.array([-33.0, -34.0])
    lon = np.array([150.0, 151.0])

    def __init__(self):
        self.reach_calls = []

    def reach(self, starts, basis):
        self.reach_calls.append((starts, basis))
        return dict(left=np.array([10.0, 0.0]), full=100.0, outline=[0, 5])

    def nearest(self, pts, basis, want_who=True):
        return np.array([5.0, np.inf]), np.array([0, 0])

    def stretches(self, d, who, basis):
        return [(0, 1, 123.456, 1)]

    def shade(self, val, vmax, bbox, size, km=8):
        return np.array([[0, 255], [128, 1]], np.uint8), np.full((2, 2), 255, np.uint16)


class ReachTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_reach_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(AR.app_config, "get", side_effect=lambda k, d=None: CONFIG.get(k, d))
        p.start()
        self.addCleanup(p.stop)
        self.R = AR.ReachService(os.path.join(HERE, "..", "Tools"), self.tmp)
        self.g = FakeGraph()
        for name, value in (("meta", lambda: {"built": "made-up"}), ("_graph", lambda: self.g)):
            p = mock.patch.object(self.R, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_png(self):
        rgba = np.zeros((3, 2, 4), np.uint8)
        rgba[1, 1] = (1, 2, 3, 4)
        w, h, rows = read_png(AR.png_bytes(rgba))
        self.assertEqual((w, h), (2, 3))
        self.assertEqual(rows[1][4:8], bytes([1, 2, 3, 4]))

    def test_colour_tables(self):
        reach, gaps = AR._lut_reach(), AR._lut_gaps()
        self.assertEqual(reach.shape, (256, 4))
        self.assertEqual(reach[0, 3], 0)                    # nothing reached: clear
        self.assertGreater(reach[255, 3], reach[1, 3])
        # gaps: clear while a charge gets there (index 1..170 is under 1 x the range), shaded beyond
        self.assertEqual(gaps[170, 3], 0)
        self.assertGreater(gaps[171, 3], 0)
        self.assertEqual(tuple(gaps[255]), (232, 84, 84, 160))

    def test_car_figures(self):
        self.assertEqual((self.R.car()["km"], self.R.car()["kwh"]), (560.0, 85.0))      # defaults with no profile
        with open(os.path.join(self.tmp, "car_profile.json"), "w") as f:
            json.dump({"VIN-MADE-UP": {"usable_battery_capacity_kwh": {"kwh_used_for_range": 78.5,
                                                                       "live_reading": {"max_range_km": 512}}}}, f)
        c = self.R.car()
        self.assertEqual((c["km"], c["kwh"]), (512.0, 78.5))
        with open(os.path.join(self.tmp, "car_profile.json"), "w") as f:
            f.write("{broken")
        self.assertEqual(self.R.car()["km"], 560.0)

    @unittest.expectedFailure
    def test_car_profile_that_is_not_an_object(self):
        """BUG (found by this test, minor): car() falls back to its defaults for an unreadable car_profile.json, but one that
        holds a list instead of an object raises AttributeError (not in its except list), so the range map fails instead."""
        with open(os.path.join(self.tmp, "car_profile.json"), "w") as f:
            f.write("[]")
        self.assertEqual(self.R.car()["km"], 560.0)

    def test_reach_uses_the_charge_above_the_reserve_and_keeps_the_result(self):
        body = dict(pts=[[-33.0, 150.0, "A"], [-34.0, 151.0], [None, 1]], frac=0.8, reserve=0.1, basis="km")
        out = self.R.compute(body)
        self.assertEqual(len(self.g.reach_calls), 1)
        starts, basis = self.g.reach_calls[0]
        self.assertEqual(basis, "km")
        self.assertEqual(len(starts), 2)                    # the point with no latitude is dropped
        self.assertAlmostEqual(starts[0][2], 0.7 * 560.0)
        self.assertEqual((out["unit"], out["n"], out["outline"]), ("km", 2, ["A"]))
        self.assertTrue(out["img"].startswith("data:image/png;base64,"))
        self.R.compute(dict(body, bbox=[-40, 140, -30, 155]))     # only the box moved
        self.assertEqual(len(self.g.reach_calls), 1)

    def test_charge_not_above_the_reserve(self):
        out = self.R.compute(dict(pts=[[-33.0, 150.0, "A"]], frac=0.1, reserve=0.2))
        self.assertIn("not above the reserve", out["error"])
        self.assertEqual(self.R.compute(dict(pts=[])), dict(empty=True))

    def test_values_are_clamped(self):
        self.R.compute(dict(pts=[[-33.0, 150.0, "A"]], frac=5, reserve=-1, basis="kwh"))
        self.assertAlmostEqual(self.g.reach_calls[0][0][0][2], 1.0 * 85.0)      # frac held to 1, reserve to 0

    def test_gaps(self):
        out = self.R.compute(dict(pts=[[-33.0, 150.0, "A"], [-34.0, 151.0, "B"]], mode="gaps", frac=0.1, reserve=0.1))
        self.assertAlmostEqual(out["safe"], 0.05 * 560.0)          # never below 5% of a charge
        self.assertAlmostEqual(out["vmax"], round(1.5 * 0.05 * 560.0, 1))
        self.assertEqual(out["stretches"], [dict(a="A", b="B", cost=123.5, lat=-34.0, lon=151.0)])

    def test_no_road_network(self):
        with mock.patch.object(self.R, "meta", lambda: None):
            self.assertEqual(self.R.compute(dict(pts=[[1, 2]])), dict(error="needs_graph"))
            self.assertEqual(self.R.at(dict(pts=[[1, 2]])), dict(error="needs_graph"))


# ------------------------------------------------------------------------------------------------------------ ABRP
class AbrpTest(unittest.TestCase):
    def test_arrival_charge(self):
        soc, kwh = AB.estimate_arrival_soc(80, 100, detour_km=10, consumption_wh_km=150, usable_kwh=75)
        self.assertAlmostEqual(kwh, 16.5)
        self.assertAlmostEqual(soc, 58.0)
        soc, kwh = AB.estimate_arrival_soc(50, -20, consumption_wh_km=150, usable_kwh=75)   # a negative distance is no distance
        self.assertEqual((soc, kwh), (50.0, 0.0))
        self.assertLess(AB.estimate_arrival_soc(10, 500)[0], 0)

    def test_badges(self):
        cases = [(25, "ok"), (24.6, "ok"), (24.4, "warning"), (10, "warning"), (9.4, "critical"), (-0.4, "critical"), (-0.6, "unreachable")]
        for soc, cat in cases:
            with self.subTest(soc=soc):
                self.assertEqual(AB.format_soc_badge(soc)[1], cat)
        self.assertIn("Req Stop", AB.format_soc_badge(-5)[0])
        self.assertEqual(AB.format_soc_badge(15, target_min_soc=20)[1], "critical")

    def test_deep_link(self):
        with mock.patch.object(AB, "load_abrp_config", return_value={"vehicle_typecode": "made:up", "target_arrival_soc": 15}):
            url = AB.generate_abrp_deep_link((-33.1234567, 150.0), (-34.0, 151.0), via_coords=[(-33.5, 150.5)], max_soc=90)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        self.assertEqual(q, {"origin": "-33.123457,150.000000", "destination": "-34.000000,151.000000", "origin_soc": "90",
                             "target_soc": "15", "vehicle_model": "made:up", "via": "-33.500000,150.500000", "max_soc": "90"})
        with mock.patch.object(AB, "load_abrp_config", return_value={}):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(AB.generate_abrp_deep_link((0, 0), (1, 1), departure_soc=55)).query))
        self.assertEqual((q["origin_soc"], q["target_soc"], q["vehicle_model"]), ("55", "10", AB.DEFAULT_VEHICLE_TYPECODE))


if __name__ == "__main__":
    unittest.main()
