#!/usr/bin/env python3
"""No made-up fuel price: with no fuel data, get_fuel_price() gives None and
every caller shows no cost (or says there is no fuel price) instead of a
guessed $/L. With fuel data, the real price is used."""
import contextlib
import datetime
import io
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "commands"))

from lib import fuel_common as FC  # noqa: E402
from lib.charging_analyzer import ChargingAnalyzer  # noqa: E402
import sync_fuel_history as SFH  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
OLD_DEFAULTS = ("$1.95", "$2.05", "$2.20", "$2.10", "$1.90", "$1.10")
# Prices used for the "with data" case (cents/L) - none equal an old default.
PRICES = {"U91": 187.0, "P98": 213.0, "DL": 199.0, "P95": 205.0, "E10": 183.0, "LPG": 99.0}
DAY = "2026-10-15"


def plain(text):
    return ANSI.sub("", text)


class FuelDirMixin:
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def add_prices(self):
        conn = FC.get_db_connection(self.d)
        for code, cents in PRICES.items():
            conn.execute(
                "INSERT INTO fuel_daily_averages (date, fuel_type, region, avg_price_cents, sample_count) "
                "VALUES (?, ?, 'all', ?, 100)", (DAY, code, cents))
        conn.commit()
        conn.close()

    def assertNoGuess(self, text):
        for p in OLD_DEFAULTS:
            self.assertNotIn(p + "/L", text)


class GetFuelPriceTest(FuelDirMixin, unittest.TestCase):
    def test_no_data_is_none(self):
        for code in ("U91", "P95", "P98", "DL", "Diesel", "E10", "LPG"):
            self.assertIsNone(FC.get_fuel_price(DAY, code, tessie_dir=self.d), code)

    def test_with_data_is_real_price(self):
        self.add_prices()
        self.assertAlmostEqual(FC.get_fuel_price(DAY, "U91", tessie_dir=self.d), 1.87)
        # Another date falls back to the nearest real reading, not a default.
        self.assertAlmostEqual(FC.get_fuel_price("2025-01-01", "P98", tessie_dir=self.d), 2.13)


class RunningCostTest(FuelDirMixin, unittest.TestCase):
    spec = {"name": "Test car", "fuel_type": "U91", "consumption": {"combined_l_per_100km": 10.0}}

    def test_single_vehicle_no_data(self):
        c = FC.calculate_single_vehicle_running_cost(self.spec, 100.0, DAY, urban_pct=1.0, highway_pct=0.0, tessie_dir=self.d)
        self.assertIsNone(c["fuel_price_per_l"])
        self.assertIsNone(c["fuel_cost"])
        self.assertIsNone(c["estimated_cost"])
        self.assertIsNone(c["total_running_cost"])
        self.assertAlmostEqual(c["litres_used"], 10.0)

    def test_single_vehicle_with_data(self):
        self.add_prices()
        c = FC.calculate_single_vehicle_running_cost(self.spec, 100.0, DAY, urban_pct=1.0, highway_pct=0.0, tessie_dir=self.d)
        self.assertAlmostEqual(c["fuel_price_per_l"], 1.87)
        self.assertAlmostEqual(c["fuel_cost"], 18.7)
        self.assertAlmostEqual(c["total_running_cost"], 18.7 + c["non_fuel_cost"])

    def test_ice_comparison_no_data(self):
        res = FC.calculate_ice_comparison_costs(100.0, DAY, tessie_dir=self.d)
        self.assertTrue(res)
        for v in res.values():
            self.assertIsNone(v["fuel_cost"])
            self.assertIsNone(v["fuel_price_per_l"])

    def test_ice_comparison_with_data(self):
        self.add_prices()
        res = FC.calculate_ice_comparison_costs(100.0, DAY, tessie_dir=self.d)
        for v in res.values():
            self.assertIsNotNone(v["fuel_cost"])
            self.assertGreater(v["fuel_cost"], 0)


class DriveComparisonTest(FuelDirMixin, unittest.TestCase):
    """format_drive_fuel_comparison: the block both drive views print."""

    def test_no_data_says_no_price(self):
        lines = FC.format_drive_fuel_comparison(50.0, DAY, kwh_used=8.0, duration_mins=40, tessie_dir=self.d)
        text = plain("\n".join(lines))
        self.assertIn("No fuel prices held", text)
        self.assertNotIn("Saved $", text)
        self.assertNotIn("/L =", text)
        self.assertNoGuess(text)

    def test_with_data_shows_costs(self):
        self.add_prices()
        lines = FC.format_drive_fuel_comparison(50.0, DAY, kwh_used=8.0, duration_mins=40, tessie_dir=self.d)
        text = plain("\n".join(lines))
        self.assertIn("U91: $1.87/L", text)
        self.assertIn("Diesel: $1.99/L", text)
        self.assertIn("/L =", text)
        self.assertNotIn("No fuel prices held", text)

    def test_partial_data_marks_missing_fuel(self):
        conn = FC.get_db_connection(self.d)
        conn.execute("INSERT INTO fuel_daily_averages (date, fuel_type, region, avg_price_cents, sample_count) "
                     "VALUES (?, 'U91', 'all', 187.0, 100)", (DAY,))
        conn.commit()
        conn.close()
        lines = FC.format_drive_fuel_comparison(50.0, DAY, kwh_used=8.0, duration_mins=40, tessie_dir=self.d)
        text = plain("\n".join(lines))
        self.assertIn("U91: $1.87/L", text)
        self.assertIn("Diesel: no price", text)
        self.assertIn("no fuel price", text)   # diesel rows
        self.assertNoGuess(text)


class SyncFuelHistoryTest(FuelDirMixin, unittest.TestCase):
    def run_quiet(self, fn, *a, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn(*a, **kw)
        return plain(out.getvalue())

    def test_status_no_data(self):
        text = self.run_quiet(SFH.print_status, self.d)
        self.assertIn("none held yet", text)
        self.assertNoGuess(text)

    def test_status_with_data(self):
        self.add_prices()
        # print_status asks for today's price; nearest real reading is DAY.
        text = self.run_quiet(SFH.print_status, self.d)
        self.assertIn("U91: $1.87/L", text)
        self.assertIn("Diesel: $1.99/L", text)

    def test_query_no_data(self):
        text = self.run_quiet(SFH.query_price, DAY, "U91", "all", self.d)
        self.assertIn("no price held", text)
        self.assertIn("no cost (no fuel price)", text)
        self.assertNoGuess(text)

    def test_query_with_data(self):
        self.add_prices()
        text = self.run_quiet(SFH.query_price, DAY, "U91", "all", self.d)
        self.assertIn("$1.870/L", text)
        self.assertNotIn("no cost", text)


def _analyzer(tessie_dir):
    a = ChargingAnalyzer.__new__(ChargingAnalyzer)
    a.tessie_data_home = tessie_dir
    a.invoices = []
    a.invoice_dirs = []
    a.config_file = None
    a.config = {}
    a.reconciled_sessions = []
    a._load_drive_totals_in_period = lambda start, end: {"distance_km": 400.0, "energy_used_kwh": 60.0}
    return a


def _session(day):
    return {
        "is_supercharger": True, "is_fast_charger": True, "emoji": "⚡",
        "dispenser_kwh": 40.0, "battery_kwh": 36.0, "invoice_cost": 20.0, "tessie_cost": 20.0,
        "range_added_km": 240.0, "datetime": datetime.datetime.fromisoformat(day + "T10:00:00"),
        "status": "MATCHED ✅", "network": "Tesla Supercharger", "location": "Somewhere",
    }


class ChargingSummaryTest(FuelDirMixin, unittest.TestCase):
    def summary(self, compare_all=False):
        a = _analyzer(self.d)
        sessions = [_session(DAY), _session("2026-10-20")]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            a.print_summary(filtered_sessions=sessions, selected_vehicle="petrol_standard", compare_all=compare_all)
        return plain(out.getvalue())

    def test_summary_no_data(self):
        text = self.summary()
        self.assertRegex(text, r"Estimated ICE Equivalent: No fuel price held")
        self.assertRegex(text, r"Projected Annual Savings vs .*: No fuel price held")
        self.assertNotIn("Net Savings: $", text)
        self.assertNoGuess(text)

    def test_summary_with_data(self):
        self.add_prices()
        text = self.summary()
        self.assertIn("@ ~$1.87/L", text)
        self.assertIn("Net Savings: $", text)
        self.assertRegex(text, r"Projected Annual Savings vs .*: \$[\d,]+\.\d\d AUD/yr")
        self.assertNotIn("No fuel price held", text)

    def test_matrix_no_data(self):
        text = self.summary(compare_all=True)
        self.assertIn("COMPARISON MATRIX", text)
        matrix = text.split("COMPARISON MATRIX", 1)[1].split("NETWORK & LOCATION", 1)[0]
        self.assertIn("no price", matrix)
        self.assertNotRegex(matrix, r"\$\d+\.\d\d/L")
        self.assertNoGuess(matrix)

    def test_matrix_with_data(self):
        self.add_prices()
        text = self.summary(compare_all=True)
        matrix = text.split("COMPARISON MATRIX", 1)[1].split("NETWORK & LOCATION", 1)[0]
        self.assertIn("$1.87/L", matrix)
        self.assertIn("$1.99/L", matrix)
        self.assertNotIn("no price", matrix)


if __name__ == "__main__":
    unittest.main()
