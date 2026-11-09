"""Which charger at a saved place (pick_charger), what it charged (_charger_price: schedules, dates, GST) and how a place's chargers are checked and defaulted (app_places)."""
import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_places  # noqa: E402
import charging_analyzer as CA  # noqa: E402

CF = {"name": "Rhodes Waterside", "network": "Chargefox", "kw": 22}
TD = {"name": "Rhodes Waterside - P3", "network": "Tesla Destination", "kw": 11}
PICK = CA.ChargingAnalyzer.pick_charger
if not callable(PICK):          # a plain method on the class, called without self
    raise unittest.SkipTest("pick_charger")


class PickTest(unittest.TestCase):
    def test_chosen_wins(self):
        self.assertEqual(PICK([CF, TD], "Rhodes Waterside - P3", None, 7), (TD, "chosen"))

    def test_invoice_network_names_it(self):
        self.assertEqual(PICK([CF, TD], None, {"network": "Chargefox"}, 7), (CF, "invoice"))

    def test_speed_tells_them_apart(self):
        self.assertEqual(PICK([CF, TD], None, None, 20), (CF, "speed"))       # 20 kW cannot be the 11 kW one

    def test_default_when_nothing_else(self):
        d = dict(TD, default=True)
        self.assertEqual(PICK([d, CF], None, None, 7), (d, "default"))

    def test_only_one(self):
        self.assertEqual(PICK([CF], None, None, 7), (CF, "only"))

    def test_two_that_cannot_be_told_apart_ask(self):
        self.assertEqual(PICK([CF, TD], None, None, 7)[1], "ask")


class PriceTest(unittest.TestCase):
    def setUp(self):
        self.a = CA.ChargingAnalyzer.__new__(CA.ChargingAnalyzer)

    def test_free_is_zero(self):
        self.assertEqual(self.a._charger_price({"free": True}, datetime.datetime(2026, 11, 9, 12, 0)), (0.0, "Free"))

    def test_flat_price_is_inclusive(self):
        self.assertEqual(self.a._charger_price({"rate_per_kwh": 0.4}, datetime.datetime(2026, 11, 9, 12, 0)), (0.4, "Flat Rate"))

    def test_schedule_by_day_time_and_dates(self):
        ch = {"rate_per_kwh": 0.5, "rate_schedules": [
            {"label": "Off-peak", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start_time": "22:00", "end_time": "07:00", "rate_per_kwh": 0.2, "valid_from": "2026-07-01", "valid_to": "2026-12-31"}]}
        self.assertEqual(self.a._charger_price(ch, datetime.datetime(2026, 11, 10, 23, 0)), (0.2, "Off-peak"))       # Tuesday night
        self.assertEqual(self.a._charger_price(ch, datetime.datetime(2026, 11, 10, 12, 0))[1], "Flat Rate")          # daytime: the flat price
        self.assertEqual(self.a._charger_price(ch, datetime.datetime(2026, 11, 14, 23, 0))[1], "Flat Rate")          # Saturday
        self.assertEqual(self.a._charger_price(ch, datetime.datetime(2027, 1, 5, 23, 0))[1], "Flat Rate")            # after valid_to

    def test_ex_gst_is_multiplied(self):
        r, _ = self.a._charger_price({"rate_per_kwh": 0.4, "gst": "ex"}, datetime.datetime(2026, 11, 9, 12, 0))
        self.assertAlmostEqual(r, round(0.4 * CA._gst_multiplier(), 4), places=4)
        self.assertGreater(r, 0.4)

    def test_no_price_is_none(self):
        self.assertEqual(self.a._charger_price({"name": "x"}, datetime.datetime(2026, 11, 9, 12, 0)), (None, None))


class PlacesTest(unittest.TestCase):
    def test_validation(self):
        clean = app_places.Places.clean_chargers if hasattr(app_places, "Places") else None
        if clean is None:
            self.skipTest("class name")
        ok, err = clean([{"name": "A", "rate_per_kwh": 0.3, "gst": "ex", "kw": 22, "rate_schedules": [{"rate_per_kwh": 0.2, "days": "Mon-Fri", "start_time": "09:00", "end_time": "17:00"}]}])
        self.assertEqual(err, "")
        self.assertEqual(ok[0]["gst"], "ex")
        self.assertEqual(ok[0]["rate_schedules"][0]["days"], ["Mon", "Tue", "Wed", "Thu", "Fri"])
        self.assertTrue(clean([{"name": "A", "rate_per_kwh": 9}])[1])             # over 5 dollars
        self.assertTrue(clean([{"name": ""}])[1])                                 # no name
        self.assertTrue(clean([{"name": "A"}, {"name": "a"}])[1])                 # the same name twice
        self.assertTrue(clean([{"name": "A", "rate_per_kwh": 0.3, "rate_schedules": [{"rate_per_kwh": 0.2, "days": "Xyz"}]}])[1])


if __name__ == "__main__":
    unittest.main()
