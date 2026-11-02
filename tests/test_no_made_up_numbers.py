#!/usr/bin/env python3
"""Money, energy and range figures come from the owner's data or are None ("unknown" / "no price"), never a typed-in stand-in.
Each test covers one fix listed in docs/MADE_UP_NUMBERS_2026-11.md. Made-up data in temporary folders only; no real config or data folder."""
import csv
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools")
sys.path.insert(0, TOOLS)
sys.path.insert(1, os.path.join(TOOLS, "commands"))

from lib import fuel_common as FC  # noqa: E402
from lib import car_figures as CF  # noqa: E402
from lib import abrp_common as AB  # noqa: E402
from lib import car_efficiency as CE  # noqa: E402
from lib import osm_roads_graph as G  # noqa: E402
from lib.charging_analyzer import ChargingAnalyzer, format_ice_equivalent  # noqa: E402


def write_drives(d, rows):
    """rows: (started_utc, km, kwh)"""
    os.makedirs(os.path.join(d, "drives"), exist_ok=True)
    with open(os.path.join(d, "drives", "drives_master.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Started At (UTC)", "Distance (km)", "Total Energy Used (kWh)"])
        w.writerows(rows)


def write_health(d, entries):
    with open(os.path.join(d, "battery_health_log.json"), "w") as f:
        json.dump(entries, f)


class FuelPriceTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_no_fuel_data_is_no_price(self):
        self.assertIsNone(FC.get_fuel_price("2026-06-01", "U91", tessie_dir=self.d))
        self.assertIsNone(FC.get_fuel_price("2026-06-01", "Diesel", tessie_dir=self.d))

    def test_a_held_price_is_returned(self):
        conn = FC.get_db_connection(self.d)
        conn.execute("INSERT INTO fuel_daily_averages (date, fuel_type, region, avg_price_cents, sample_count) VALUES (?,?,?,?,?)",
                     ("2026-06-01", "U91", "all", 201.5, 100))
        conn.commit()
        conn.close()
        self.assertAlmostEqual(FC.get_fuel_price("2026-06-01", "U91", tessie_dir=self.d), 2.015)

    def test_running_cost_without_price_or_consumption_is_none(self):
        spec = {"name": "X", "fuel_type": "U91", "consumption": {"combined_l_per_100km": 8.0}}
        c = FC.calculate_single_vehicle_running_cost(spec, 100.0, "2026-06-01", urban_pct=1.0, highway_pct=0.0, tessie_dir=self.d)
        self.assertIsNone(c["fuel_price_per_l"])
        self.assertIsNone(c["fuel_cost"])
        self.assertAlmostEqual(c["litres_used"], 8.0)
        c = FC.calculate_single_vehicle_running_cost({"name": "Y", "fuel_type": "U91"}, 100.0, "2026-06-01",
                                                     urban_pct=1.0, highway_pct=0.0, tessie_dir=self.d)
        self.assertIsNone(c["l_per_100km"])
        self.assertIsNone(c["litres_used"])

    def test_vehicle_l_per_100km(self):
        self.assertEqual(FC.vehicle_l_per_100km({}), (None, None, None))
        self.assertEqual(FC.vehicle_l_per_100km({"consumption": {"combined_l_per_100km": 7.0, "urban_l_per_100km": 9.0}}), (7.0, 9.0, 7.0))
        self.assertEqual(FC.vehicle_l_per_100km({"l_per_100km": 6.0}), (6.0, 6.0, 6.0))

    def test_drive_comparison_says_no_price(self):
        text = "\n".join(FC.format_drive_fuel_comparison(20.0, "2026-06-01", kwh_used=3.0, tessie_dir=self.d))
        self.assertIn("no price", text)
        for made_up in ("$1.95", "$2.20", "$2.05"):
            self.assertNotIn(made_up, text)


class CarFiguresTest(unittest.TestCase):
    def test_nothing_is_none(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(CF.latest_battery(d))
        self.assertIsNone(CF.usable_kwh(d))
        self.assertIsNone(CF.driving_wh_per_km(d))
        self.assertIsNone(CF.usable_kwh(None))

    def test_from_the_data(self):
        d = tempfile.mkdtemp()
        write_health(d, [dict(synced_at="2026-01-01T00:00:00", capacity_kwh=80.0, max_range_km=500.0),
                         dict(synced_at="2026-06-01T00:00:00", capacity_kwh=78.0, max_range_km=490.0)])
        write_drives(d, [(1000, 100.0, 15.0), (2000, 50.0, 10.0), (3000, 0, 1.0), (4000, 10.0, "")])
        self.assertEqual(CF.usable_kwh(d), 78.0)
        self.assertEqual(CF.latest_battery(d)["max_range_km"], 490.0)
        self.assertAlmostEqual(CF.driving_wh_per_km(d), 25.0 * 1000 / 150.0)        # rows without distance or energy left out
        self.assertAlmostEqual(CF.driving_wh_per_km(d, 1500, 2500), 200.0)


class IceEquivalentTest(unittest.TestCase):
    def analyzer(self, d):
        a = ChargingAnalyzer.__new__(ChargingAnalyzer)
        a.tessie_data_home, a.tessie_dirs = d, [d]
        return a

    def sessions(self):
        dt = datetime.datetime(2026, 6, 1, 10, 0)
        return [dict(battery_kwh=20.0, datetime=dt, invoice_cost=None, tessie_cost=6.0),
                dict(battery_kwh=10.0, datetime=dt, invoice_cost=4.0, tessie_cost=0.0),
                dict(battery_kwh=0.0, datetime=dt, invoice_cost=9.0, tessie_cost=0.0)]     # an invoice Tessie never saw

    def test_from_real_figures(self):
        a = self.analyzer(tempfile.mkdtemp())
        ice = a._ice_equivalent(self.sessions(), 8.0, "U91", 200.0, lambda dt, fuel_type, tessie_dir: 2.0)
        # 30 kWh / 200 Wh/km = 150 km; 150 km x 8 L/100km x $2 = $24; the EV side is the two charges counted ($10)
        self.assertAlmostEqual(ice["spend"], 24.0)
        self.assertAlmostEqual(ice["ev_spend"], 10.0)
        self.assertEqual((ice["counted"], ice["left_out"]), (2, 1))
        line = format_ice_equivalent(ice, "Car", "U91", 8.0, ice["spend"] - ice["ev_spend"])
        self.assertIn("$24.00", line)
        self.assertIn("+$14.00", line)

    def test_unknown_not_zero(self):
        a = self.analyzer(tempfile.mkdtemp())
        price = lambda dt, fuel_type, tessie_dir: 2.0          # noqa: E731
        cases = [(a._ice_equivalent(self.sessions(), 8.0, "U91", 200.0, lambda *x, **k: None), "FuelCheck"),
                 (a._ice_equivalent(self.sessions(), None, "U91", 200.0, price), "L/100km"),
                 (a._ice_equivalent(self.sessions(), 8.0, "U91", None, price), "kWh into km"),
                 (a._ice_equivalent([self.sessions()[2]], 8.0, "U91", 200.0, price), "battery kWh")]
        for ice, why in cases:
            self.assertIsNone(ice["spend"])
            line = format_ice_equivalent(ice, "Car", "U91", 8.0, None)
            self.assertIn("unknown", line)
            self.assertIn(why, line)
            self.assertNotIn("$0.00", line)

    def test_real_wh_per_km(self):
        d = tempfile.mkdtemp()
        a = self.analyzer(d)
        self.assertEqual(a._real_wh_per_km({"distance_km": 100.0, "energy_used_kwh": 16.0}), 160.0)
        self.assertIsNone(a._real_wh_per_km(None))                 # no drives at all
        write_drives(d, [(1, 100.0, 18.0)])
        self.assertEqual(a._real_wh_per_km(None), 180.0)          # the window has none: all logged drives


class AbrpTest(unittest.TestCase):
    def test_arrival_unknown_without_figures(self):
        self.assertEqual(AB.estimate_arrival_soc(None, 100.0, 0.0, 150.0, 80.0)[0], None)
        self.assertEqual(AB.estimate_arrival_soc(80, 100.0, 0.0, None, 80.0), (None, None))
        self.assertIsNone(AB.estimate_arrival_soc(80, 100.0, 0.0, 150.0, None)[0])
        arr, e = AB.estimate_arrival_soc(80, 100.0, 0.0, 150.0, 75.0)
        self.assertAlmostEqual(e, 15.0)
        self.assertAlmostEqual(arr, 60.0)

    def test_pack_and_consumption_from_the_data(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(AB.car_usable_kwh({}, {}, data_dir=d))
        self.assertIsNone(AB.car_consumption_wh_km({}, None, data_dir=d))
        write_health(d, [dict(synced_at="2026-06-01T00:00:00", capacity_kwh=77.0)])
        write_drives(d, [(1, 100.0, 16.5)])
        self.assertEqual(AB.car_usable_kwh({}, {"usable_kwh": 85}, data_dir=d), 77.0)        # the reading beats the spec sheet
        self.assertEqual(AB.car_consumption_wh_km({}, None, data_dir=d), 165.0)
        self.assertEqual(AB.car_usable_kwh({"usable_kwh": 70}, {}, data_dir=d), 70.0)        # config.json override
        self.assertEqual(AB.car_consumption_wh_km({}, {"telemetry": {"calib_ref_cons": 158.04}}, data_dir=d), 158.0)

    def test_departure_unknown_when_offline(self):
        with mock.patch.object(AB, "load_abrp_config", return_value={"default_departure_soc": "live"}), \
                mock.patch.object(AB, "fetch_live_battery_soc", return_value=(None, {})):
            soc, src, _ = AB.resolve_departure_soc(None)
        self.assertIsNone(soc)
        self.assertIn("unknown", src)

    def test_deep_link_without_soc(self):
        with mock.patch.object(AB, "load_abrp_config", return_value={"vehicle_typecode": "x", "target_arrival_soc": 10}):
            url = AB.generate_abrp_deep_link((-33.0, 151.0), (-34.0, 150.0), departure_soc=None)
            self.assertNotIn("origin_soc", url)
            self.assertIn("origin_soc=55", AB.generate_abrp_deep_link((-33.0, 151.0), (-34.0, 150.0), departure_soc=55))


class StationArrivalTest(unittest.TestCase):
    def test_no_assumed_departure(self):
        from lib.station_report_email import station_arrival_soc

        class St:
            route_progress_km, distance_km, detour_km = 50.0, 50.0, 0.0
        self.assertIsNone(station_arrival_soc(St(), {"consumption_wh_km": 160.0, "usable_kwh": 80.0}))
        self.assertAlmostEqual(station_arrival_soc(St(), {"departure_soc": 80, "consumption_wh_km": 160.0, "usable_kwh": 80.0}), 70.0)


class ReachTest(unittest.TestCase):
    def service(self, d):
        from lib.app_reach import ReachService
        os.makedirs(os.path.join(d, "roads", "graph"), exist_ok=True)
        with open(os.path.join(d, "roads", "graph", "meta.json"), "w") as f:
            json.dump({"nodes": 1}, f)
        return ReachService(TOOLS, d)

    def test_unknown_range_is_an_error_not_560_km(self):
        r = self.service(tempfile.mkdtemp())
        c = r.car()
        self.assertEqual((c["km"], c["kwh"]), (None, None))
        st = r.status()
        self.assertEqual((st["km"], st["kwh"]), (None, None))
        for basis in ("km", "kwh"):
            out = r.compute({"pts": [[-33.0, 151.0, "a"]], "basis": basis})
            self.assertIn("not known", out.get("error", ""))

    def test_battery_health_fills_in(self):
        d = tempfile.mkdtemp()
        write_health(d, [dict(synced_at="2026-06-01T00:00:00", capacity_kwh=76.5, max_range_km=480.0)])
        c = self.service(d).car()
        self.assertEqual((c["km"], c["kwh"]), (480.0, 76.5))
        with open(os.path.join(d, "car_profile.json"), "w") as f:
            json.dump({"car": {"usable_battery_capacity_kwh": {"kwh_used_for_range": 79.0, "live_reading": {"max_range_km": 501.0}}}}, f)
        c = self.service(d).car()
        self.assertEqual((c["km"], c["kwh"]), (501.0, 79.0))


class RoadFitTest(unittest.TestCase):
    def band(self, lo, hi, eff, km=100.0):
        return {"speed_range_kmh": [lo, hi], "confidence": "high", "total_distance_km": km, "avg_efficiency_kwh_per_100km": eff}

    def test_no_bands_no_fit(self):
        self.assertIsNone(G.kwh_per_100km_fit({}))
        self.assertIsNone(G.kwh_per_100km_fit({"speed_vs_efficiency_grade_corrected": []}))
        g = G.RoadGraph.__new__(G.RoadGraph)
        g.fit = None
        with self.assertRaises(ValueError):
            g._f("kwh", [60.0])
        self.assertEqual(list(g._f("km", [60.0])), [1.0])

    def test_few_bands_flat_average(self):
        p = {"speed_vs_efficiency_grade_corrected": [self.band(40, 50, 14.0, 100), self.band(90, 100, 18.0, 300)]}
        c0, c1 = G.kwh_per_100km_fit(p)
        self.assertAlmostEqual(c0, 17.0)
        self.assertEqual(c1, 0.0)

    def test_fit_from_bands(self):
        bands = [self.band(v - 5, v + 5, 12.0 + 0.001 * v * v) for v in (30, 60, 90, 110)]
        c0, c1 = G.kwh_per_100km_fit({"speed_vs_efficiency_grade_corrected": bands})
        self.assertAlmostEqual(c0, 12.0, places=3)
        self.assertAlmostEqual(c1, 0.001, places=5)


class MassTest(unittest.TestCase):
    def test_no_mass_no_correction(self):
        self.assertIsNone(CE.vehicle_mass_kg_for_profile({}))
        seg = {"energy_kwh": 2.0, "distance_km": 10.0, "elevation_change_m": 50.0}
        self.assertIsNone(CE.grade_corrected_efficiency_kwh_per_100km(seg, None))
        self.assertIsNone(CE.potential_energy_kwh(None, 50.0))
        self.assertIsNotNone(CE.grade_corrected_efficiency_kwh_per_100km(seg, 2100))


class ChargerPriceTest(unittest.TestCase):
    def test_evx_feed_has_no_price(self):
        from providers.chargers.chargers_evx import parse_evx_location_record
        rec = parse_evx_location_record({"id": 1, "name": "Pole", "streetaddress": "1 Test St, Testville NSW 2000",
                                         "custom_fields": {"7ulej7jp2u": "P1"}, "loc_lat": "-33.9", "loc_long": "151.2"})
        t = rec["tariffs"]
        self.assertIsNone(t["per_kwh_gross"])
        self.assertEqual(t["schedule"], [])
        self.assertNotIn("tessie_cost_config", rec)

    def test_bppulse_feed_has_no_price(self):
        from providers.chargers.chargers_bppulse import parse_bppulse_location
        rec = parse_bppulse_location({"id": 9, "identifier": "BP9", "city": "Testville", "streetAndNumber": "1 Test St",
                                      "zip": "2000", "province": "NSW", "lat": -33.9, "lng": 151.2})
        self.assertIsNone(rec["tariffs"]["per_kwh_gross"])
        self.assertEqual(rec["tariffs"]["schedule"], [])
        self.assertEqual(rec["tariffs"]["rate_range"], "")

    def test_unknown_station_is_not_free(self):
        from providers.chargers.chargers_base import UnifiedStationRecord
        r = UnifiedStationRecord(key="K", name="N", network="EVX", pricing_model="unknown")
        self.assertEqual(r.formatted_rate(), "-")
        self.assertIsNone(r.to_dict()["tessie_cost_config"]["per_kwh_flat"])

    def test_expected_rate_unknown_for_a_station_without_price(self):
        a = ChargingAnalyzer.__new__(ChargingAnalyzer)
        for k in ("superchargers_archived", "charging_archived", "consolidated_archived", "chargefox_archived", "evie_archived",
                  "evx_archived", "plugshare_archived"):
            setattr(a, k, {})
        from providers.chargers.chargers_evx import parse_evx_location_record
        rec = parse_evx_location_record({"id": 1, "name": "Pole", "streetaddress": "1 Test St, Testville NSW 2000", "custom_fields": {}})
        rec.pop("name", None)
        out = a.get_expected_tariff_rate(rec, datetime.datetime(2026, 6, 1, 10, 0))
        self.assertIsNone(out["rate_per_kwh"])
        self.assertIsNone(out.get("theoretical_cost"))

    def test_tesla_fee_defaults_gone(self):
        # The Tesla scraper's fee defaults are inside one long function; this guards the source against the old stand-ins.
        src = open(os.path.join(TOOLS, "commands", "find_tesla_chargers.py"), encoding="utf-8").read()
        self.assertNotIn("idle_fee = 1.00", src)
        self.assertNotIn("congestion_fee = 0.50", src)
        self.assertIn("idle_fee = None", src)


class RoiTest(unittest.TestCase):
    def test_no_assumed_lifetime(self):
        import power_roi_report as R
        with mock.patch.object(R, "load_solar_system_config",
                               return_value={"install_date": "2019-11-28", "net_upfront_cost_aud": 10000, "system_size_kw": 10}):
            self.assertIsNone(R.resolve_solar_config()[2])
        with mock.patch.object(R, "load_solar_system_config",
                               return_value={"install_date": "2019-11-28", "net_upfront_cost_aud": 10000, "lifetime_years": 20}):
            self.assertEqual(R.resolve_solar_config()[2], 20)


if __name__ == "__main__":
    unittest.main()
