#!/usr/bin/env python3
"""test_station_report_money.py - the prices, fees and arrival battery % on the emailed charging-station report, and the
solar install file loader.

Covers the pure parts of lib/station_report_email.py (price schedules, money and duration text, the card's price / fee / trip
lines, arrival %, address, facilities summary) and lib/solar_system_config.py. Made-up stations, prices and POIs only, temp
folders, no network (the busy-times and facilities lookups are faked). Runs without config or data:
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_station_report_money.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import station_report_email as SR  # noqa: E402
from lib import solar_system_config as SSC  # noqa: E402

TOU = [{"name": "Off-Peak", "start": "00:00", "end": "08:00", "rate": 0.34},
       {"name": "Peak", "start": "08:00", "end": "23:00", "rate": 0.48},
       {"name": "Off-Peak", "start": "23:00", "end": "24:00", "rate": 0.34}]
FLAT = [{"label": "All day", "start_time": "00:00", "end_time": "24:00", "rate_per_kwh": 0.66}]


class Station:
    """A made-up station with the attributes and methods the report reads."""

    def __init__(self, **kw):
        self.source, self.network, self.name, self.key = "tesla", "Tesla", "Testville", "testville"
        self.address, self.suburb, self.state, self.postcode = "1 Test St", "Testville", "NSW", "2999"
        self.metadata, self.hardware_model, self.stalls_total, self.max_power_kw, self.plugs = {}, "V3", 8, 250, ["CCS2"]
        self.schedule, self.non_tesla_schedule, self.current_rate_per_kwh, self.rate_range = TOU, FLAT, None, ""
        self.congestion_fee_per_min = self.idle_fee_per_min = 0.0
        self.parking_fee, self.distance_km, self.lat, self.lon = "", 4.25, -30.0, 140.0
        self.access, self.pricing = "CCS2 All", (0.48, "Peak", "08:00–23:00")
        self.__dict__.update(kw)

    def speed_tier_label(self):
        return "V3"

    def access_label(self):
        return self.access

    def formatted_power(self):
        return "%d kW" % self.max_power_kw

    def get_effective_pricing(self, eval_time):
        return self.pricing


class ScheduleTextTest(unittest.TestCase):
    def test_period_over_midnight_is_joined(self):
        self.assertEqual(SR._sched_items(TOU), [("Off-Peak", "23:00", "08:00", 0.34), ("Peak", "08:00", "23:00", 0.48)])

    def test_not_joined_when_label_or_rate_differ(self):
        s = [dict(TOU[0]), dict(TOU[1]), dict(TOU[2], rate=0.30)]
        self.assertEqual(len(SR._sched_items(s)), 3)
        s = [dict(TOU[0]), dict(TOU[1]), dict(TOU[2], name="Night")]
        self.assertEqual(len(SR._sched_items(s)), 3)

    def test_other_key_names_and_missing_rates(self):
        self.assertEqual(SR._sched_items(FLAT + [{"name": "x"}]), [("All day", "00:00", "24:00", 0.66)])
        self.assertEqual(SR._sched_items([{"rate": "0.5"}]), [("Rate", "00:00", "24:00", 0.5)])
        self.assertEqual(SR._sched_items(None), [])

    def test_money(self):
        self.assertEqual(SR._money(0), "free")
        self.assertEqual(SR._money(0.444), "$0.44")
        self.assertEqual(SR._money(0.445001), "$0.45")

    def test_range(self):
        self.assertEqual(SR.sched_range(TOU), "$0.34–0.48")
        self.assertEqual(SR.sched_range(FLAT), "$0.66")
        self.assertEqual(SR.sched_range([{"rate": 0.5}, {"rate": 0.503}]), "$0.50")          # under half a cent apart: one price
        self.assertEqual(SR.sched_range([]), "")

    def test_full(self):
        self.assertEqual(SR.sched_full(TOU), "Off-Peak 23:00–08:00 $0.34 · Peak 08:00–23:00 $0.48 /kWh")
        self.assertEqual(SR.sched_full(FLAT), "$0.66/kWh all day")
        self.assertEqual(SR.sched_full([{"rate": 0}]), "free/kWh all day")
        self.assertEqual(SR.sched_full(None), "")

    def test_duration(self):
        for m, want in ((None, "0m"), (0, "0m"), (59.9, "59m"), (60, "1h 00m"), (135, "2h 15m"), (600.5, "10h 00m")):
            self.assertEqual(SR._dur(m), want, m)

    def test_rate_now(self):
        self.assertEqual(SR._rate(Station(), None), ("$0.48/kWh", "Peak (08:00–23:00)"))
        self.assertEqual(SR._rate(Station(pricing=(0.0, "Free", "24/7")), None), ("Free", "Free"))
        self.assertEqual(SR._rate(Station(pricing=(0.5, "", "00:00–24:00")), None), ("$0.50/kWh", "-"))
        self.assertEqual(SR._rate(Station(pricing=(None, None, None)), None), ("-", "-"))


class ArrivalSocTest(unittest.TestCase):
    RI = {"departure_soc": 80.0, "consumption_wh_km": 150.0, "usable_kwh": 75.0}

    def test_from_departure(self):
        st = Station(route_progress_km=100.0, detour_km=2.0)
        # 102 km * 150 Wh/km = 15.3 kWh = 20.4 % of 75 kWh
        self.assertAlmostEqual(SR.station_arrival_soc(st, self.RI), 59.6, places=6)

    def test_leg_aware(self):
        st = Station(route_progress_km=300.0, leg_departure_soc=90.0, leg_distance_km=50.0)
        self.assertAlmostEqual(SR.station_arrival_soc(st, self.RI), 80.0, places=6)            # 50 km = 10 % from the leg's 90 %

    def test_unknown_inputs_give_none(self):
        st = Station(route_progress_km=100.0)
        self.assertIsNone(SR.station_arrival_soc(st, None))
        for k in self.RI:
            self.assertIsNone(SR.station_arrival_soc(st, dict(self.RI, **{k: None})), k)
        self.assertIsNone(SR.station_arrival_soc(Station(distance_km=None), self.RI))

    def test_off_route_station_uses_its_distance(self):
        self.assertAlmostEqual(SR.station_arrival_soc(Station(distance_km=50.0), self.RI), 70.0, places=6)


class CardLinesTest(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(SR, "station_facilities_info", return_value=None),
                  mock.patch.object(SR, "station_facilities_score", return_value=None),
                  mock.patch("lib.tesla_busy_times.station_busy_profile", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def test_tesla_and_non_tesla_prices_with_rate_now(self):
        c = SR.station_card_lines(Station(), None, None)
        self.assertEqual(c["price_lines"], ["Tesla: Off-Peak 23:00–08:00 $0.34 · Peak 08:00–23:00 $0.48 /kWh (now $0.48)",
                                            "Non-Tesla: $0.66/kWh all day"])
        c = SR.station_card_lines(Station(), None, "18:00")
        self.assertTrue(c["price_lines"][0].endswith("(18:00: $0.48)"))

    def test_flat_price_has_no_rate_now(self):
        c = SR.station_card_lines(Station(schedule=FLAT, non_tesla_schedule=None), None, None)
        self.assertEqual(c["price_lines"], ["$0.66/kWh all day"])

    def test_without_a_schedule(self):
        c = SR.station_card_lines(Station(schedule=None, non_tesla_schedule=None, current_rate_per_kwh=0.55), None, None)
        self.assertEqual(c["price_lines"], ["$0.55/kWh"])
        c = SR.station_card_lines(Station(schedule=None, non_tesla_schedule=None, current_rate_per_kwh=0.0, rate_range="$0.40-0.60"), None, None)
        self.assertEqual(c["price_lines"], ["$0.40-0.60"])
        c = SR.station_card_lines(Station(schedule=None, non_tesla_schedule=None), None, None)
        self.assertEqual(c["price_lines"], ["not listed"])

    def test_non_tesla_network_shows_one_price(self):
        c = SR.station_card_lines(Station(source="plugshare", network="Testnet"), None, None)
        self.assertEqual(len(c["price_lines"]), 1)

    def test_shared_report_shows_what_another_ev_pays(self):
        c = SR.station_card_lines(Station(), None, None, share=True)
        self.assertEqual(c["price_lines"], ["Tesla & members: Off-Peak 23:00–08:00 $0.34 · Peak 08:00–23:00 $0.48 /kWh",
                                            "Non-members: $0.66/kWh all day"])
        c = SR.station_card_lines(Station(access="Tesla Only"), None, None, share=True)
        self.assertEqual((c["price_lines"], c["tesla_only"]), (["Tesla vehicles only"], True))

    def test_fees(self):
        c = SR.station_card_lines(Station(congestion_fee_per_min=1.0, idle_fee_per_min=0.5, parking_fee="$5 flat"), None, None)
        self.assertEqual(c["fees"], "Congestion $1.00/min · parking $5 flat")
        c = SR.station_card_lines(Station(idle_fee_per_min=0.5), None, None)
        self.assertEqual(c["fees"], "Idle $0.50/min")
        self.assertEqual(SR.station_card_lines(Station(), None, None)["fees"], "")

    def test_trip_line_and_arrival_level(self):
        ri = {"departure_soc": 80.0, "consumption_wh_km": 150.0, "usable_kwh": 75.0}
        st = Station(route_progress_km=120.4, detour_km=0.3)
        c = SR.station_card_lines(st, ri, None)
        self.assertEqual((c["trip"], c["arr_level"]), ("120 km along the route · arrive with 56%", "good"))
        st = Station(route_progress_km=300.0, detour_km=1.25)
        c = SR.station_card_lines(st, ri, None)                       # 301.25 km = 60.25 %: 19.75 % left
        self.assertEqual((c["trip"], c["arr_level"]), ("300 km along the route · 1.2 km detour · arrive with 20%", "warn"))
        c = SR.station_card_lines(Station(route_progress_km=360.0), ri, None)          # 72 % used, 8 % left: under the 10 % target
        self.assertEqual(c["arr_level"], "bad")
        c = SR.station_card_lines(Station(route_progress_km=500.0), ri, None)
        self.assertEqual((c["trip"], c["arr_level"]), ("500 km along the route · needs a stop before", "bad"))
        c = SR.station_card_lines(Station(route_progress_km=120.0), ri, None, share=True)
        self.assertEqual((c["trip"], c["arr_level"]), ("120 km along the route", ""))
        c = SR.station_card_lines(Station(route_progress_km=120.0), dict(ri, no_soc=True), None)
        self.assertEqual(c["trip"], "120 km along the route")
        self.assertEqual(SR.station_card_lines(Station(), None, None)["trip"], "4.2 km away")


class AddressAndFacilitiesTest(unittest.TestCase):
    def test_address(self):
        self.assertEqual(SR.station_address(Station()), "1 Test St, Testville, NSW, 2999")
        self.assertEqual(SR.station_address(Station(address="1 Test St, Testville New South Wales")), "1 Test St, Testville NSW, 2999")
        self.assertEqual(SR.station_address(Station(address="1 Test St, Testville NSW 2999")), "1 Test St, Testville NSW 2999")
        self.assertEqual(SR.station_address(Station(address="", postcode="")), "Testville, NSW")

    def test_facilities_level(self):
        for s, want in ((None, ""), (9.0, "good"), (8.0, "good"), (7.9, "warn"), (6.0, "warn"), (5.0, "low"), (3.9, "bad")):
            self.assertEqual(SR.facilities_level(s), want, s)

    def test_whole_word_names(self):
        self.assertTrue(SR._name_has("Coles Central", ("coles",)))
        self.assertFalse(SR._name_has("Colesberg Motors", ("coles",)))
        self.assertTrue(SR._name_has("BIG W Testville", ("big w",)))

    def test_poi_text(self):
        self.assertEqual(SR._poi_str({"name": "A very long name of some made up shop", "dist_m": 149.6}, 10), "A very lo… 150 m")
        self.assertEqual(SR._poi_str({"category": "Cafe"}), "Cafe 0 m")

    def test_brief(self):
        pois = [{"name": "Tesla Supercharger", "category": "Charging", "dist_m": 5},
                {"name": "Testville Shopping Centre", "category": "Mall", "dist_m": 300, "group": "Retail & Shopping"},
                {"name": "Coles", "category": "Supermarket", "dist_m": 200, "group": "Supermarkets & Groceries"},
                {"name": "Kmart", "category": "Department Store", "dist_m": 320, "group": "Retail & Shopping"},
                {"name": "Corner Grocer", "category": "Convenience", "dist_m": 50, "group": "Supermarkets & Groceries", "emoji": "🛒"},
                {"name": "Cafe One", "category": "Cafe", "dist_m": 60, "group": "Cafes & Coffee", "emoji": "☕"},
                {"name": "Cafe Two", "category": "Cafe", "dist_m": 70, "group": "Cafes & Coffee"},
                {"name": "Cafe Three", "category": "Cafe", "dist_m": 80, "group": "Cafes & Coffee"},
                {"name": "", "category": "Building", "dist_m": 10}]
        b = SR.facilities_brief({"pois": pois})
        self.assertEqual([(k, p["name"]) for k, p in b["best"]],
                         [("Shopping centre", "Testville Shopping Centre"), ("Supermarket", "Coles"), ("Dept store", "Kmart")])
        self.assertEqual([(e, g, [p["name"] for p in items], more) for e, g, items, more in b["groups"]],
                         [("🛒", "Supermarkets & Groceries", ["Corner Grocer"], 0), ("☕", "Cafes & Coffee", ["Cafe One", "Cafe Two"], 1)])
        self.assertEqual(SR.facilities_brief(None), {"best": [], "groups": []})

    def test_maps_url(self):
        with mock.patch("lib.station_facilities_common.resolve_station_physical_coords", return_value=(-30.5, 140.25, "osm")):
            self.assertEqual(SR.station_maps_url(Station()), "https://www.google.com/maps/search/-30.5,140.25")
        with mock.patch("lib.station_facilities_common.resolve_station_physical_coords", return_value=(0.0, 0.0, "")):
            self.assertIsNone(SR.station_maps_url(Station()))
        with mock.patch("lib.station_facilities_common.resolve_station_physical_coords", side_effect=RuntimeError):
            self.assertEqual(SR.station_maps_url(Station()), "https://www.google.com/maps/search/-30.0,140.0")


class SolarSystemConfigTest(unittest.TestCase):
    def test_path_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = SSC.resolve_solar_system_path(d)
            self.assertEqual(path, os.path.join(d, "Solar", "solar_system.json"))
            with self.assertRaises(FileNotFoundError) as cm:
                SSC.load_solar_system_config(d)
            self.assertIn(path, str(cm.exception))
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as fh:
                json.dump({"install_date": "2020-01-01", "net_upfront_cost_aud": 1234.5}, fh)
            self.assertEqual(SSC.load_solar_system_config(d)["net_upfront_cost_aud"], 1234.5)

    def test_default_home_is_the_tesla_data_home(self):
        with mock.patch("lib.tessie_api_common.resolve_tessie_data_home", return_value="/made/up/home"):
            self.assertEqual(SSC.resolve_solar_system_path(), os.path.join("/made/up/home", "Solar", "solar_system.json"))


if __name__ == "__main__":
    unittest.main()
