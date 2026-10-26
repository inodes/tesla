"""The site page of a charger: everything the registry holds about it reaches Places.detail (tariffs, busy times, facilities, per-source rows), for Tesla, other networks and your own."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))

import app_places

TESLA = {"id": "tesla_x", "name": "Testville, NSW", "network": "Tesla", "operator": "Tesla",
         "location": {"lat": -33.8, "lon": 151.1, "address": "1 Test St, Testville", "suburb": "Testville", "state": "NSW", "timezone": "Australia/Sydney"},
         "hardware": {"stalls": 8, "max_power_kw": 250, "tier": "V3", "connector_types": ["CCS2"]},
         "compatibility": {"tesla_only": True},
         "access": {"hours": "Available 24/7", "description": "Public\nNear the shops"},
         "tariffs": {"currency": "AUD", "pricing_model": "time_of_use",
                     "tesla_members": {"rate_schedules": [{"label": "Peak", "start_time": "09:00", "end_time": "24:00", "days": ["Mon"], "rate_per_kwh_gross": 0.59}]}},
         "views": {"tesla": {"url": "https://www.tesla.com/findus/location/supercharger/testville", "id": "findus1", "verified_at": "2026-09-01T00:00:00Z", "foo": "bar"}}}
OTHER = {"id": "ev_1", "name": "Plug Place", "network": "Evie", "location": {"lat": -33.9, "lon": 151.2, "address": "2 Plug St"}, "hardware": {"stalls": 2, "max_power_kw": 50}}


class Site(unittest.TestCase):
    def _w(self, name, obj):
        with open(os.path.join(self.t.name, name), "w") as f:
            json.dump(obj, f)

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = self.t.name
        os.makedirs(os.path.join(d, "chargers"))
        self._w("charging_stations.json", {"stations": {"tesla_x": TESLA, "ev_1": OTHER}})
        self._w("station_facilities.json", {"Tesla_Testville, NSW": {"score": 8.5, "rating": "Good", "badge": "b", "total_count": 3, "categories": {"Food": ["Cafe (10m)"]}}})
        self._w("chargers/personal_chargers.json", {"Home": {"name": "Home", "network": "Evnex", "lat": -33.7, "lon": 151.0, "costs": {"pricing_model": "time_of_use", "rate_schedules": [
            {"name": "Day", "start_time": "07:00", "end_time": "16:00", "rate_per_kwh": 0.12}, {"name": "Peak", "start_time": "16:00", "end_time": "22:00", "rate_per_kwh": 0.29}]}}})
        self.pl = app_places.Places(d)
        self.pl._events = lambda S: []

    def tearDown(self):
        self.t.cleanup()

    def test_supercharger_site(self):
        s = self.pl.site("s:Testville, NSW")
        self.assertEqual(s["hardware"]["stalls"], 8)
        self.assertEqual(s["tariffs"]["members"][0]["rate"], 0.59)
        self.assertEqual(s["facilities"]["score"], 8.5)
        self.assertEqual([x["name"] for x in s["sources"]], ["tesla.com"])
        self.assertIn(["Foo", "bar"], s["sources"][0]["rows"])
        self.assertIsNone(s["busy"])                      # no busy-time file: nothing invented

    def test_other_network_by_id(self):
        s = self.pl.site("c:ev_1")
        self.assertEqual((s["network"], s["hardware"]["kw"]), ("Evie", 50))

    def test_own_charger_has_its_rates(self):
        s = self.pl.site("p:Home")
        self.assertEqual([r["rate"] for r in s["tariffs"]["schedule"]], [0.12, 0.29])
        self.assertEqual(s["tariffs"]["range"], "$0.12 - $0.29/kWh")
        self.assertIsNone(s["tariffs"]["per_kwh"])

    def test_home_is_priced_with_the_household_tariff(self):
        import charger_site
        periods = [
            {"effective_from": "1970-01-01", "usage_rate_per_kwh": 0.25, "supply_charge_per_day": 1.0, "solar_feed_in_tier1_rate_per_kwh": 0.12, "effective_to": "2026-09-06"},
            {"effective_from": "2026-09-07", "effective_to": None, "tariff_type": "tou", "peak_rate_per_kwh": 0.25, "offpeak_rate_per_kwh": 0.15, "peak_months": [11, 12, 1],
             "peak_hour_start": 15, "peak_hour_end": 21, "supply_charge_per_day": 2.0, "solar_feed_in_tier1_rate_per_kwh": 0.1, "solar_feed_in_tier2_rate_per_kwh": 0.04}]
        t = charger_site.agl_tariff(periods, 1.1, "2026-10-12", "AGL", "Solar VIP")
        self.assertEqual((t["retailer"], t["since"], t["model"]), ("AGL Solar VIP", "2026-09-07", "time_of_use"))
        peak = t["schedule"][0]
        self.assertEqual((peak["label"], peak["start"], peak["end"], peak["months"], peak["rate"], peak["net"]), ("Peak", "15:00", "21:00", ["Jan", "Nov", "Dec"], 0.275, 0.25))
        self.assertEqual(t["schedule"][-1]["months"][0], "Feb")            # every other month is off-peak all day
        self.assertEqual([h["frm"] for h in t["history"]], ["2026-09-07", "1970-01-01"])
        self.assertEqual(t["history"][1]["peak"], 0.275)                   # an older flat period shows its one rate as both
        self.assertIn(["Supply charge", "$2.2000/day"], t["fixed"])
        self.assertIsNone(charger_site.agl_tariff(periods, 1.1, "1960-01-01"))
        own = charger_site.personal_info("Home", {"name": "Home", "type": "home", "costs": {"rate_schedules": [{"name": "Old card", "rate_per_kwh": 0.29}]}}, t)
        self.assertEqual(own["tariffs"]["retailer"], "AGL Solar VIP")      # not the charger's hand-entered card

    def test_unknown_keys_have_no_site(self):
        self.assertIsNone(self.pl.site("s:Nowhere"))
        self.assertIsNone(self.pl.site("p:Cafe"))

    def test_detail_carries_the_site(self):
        class S:
            d = self.t.name
            def charges(s): return []
            def drives(s): return []
            def idles(s): return []
        self.assertEqual(self.pl.detail(S(), "s:Testville, NSW")["site"]["name"], "Testville, NSW")


if __name__ == "__main__":
    unittest.main()
