#!/usr/bin/env python3
"""No fuel price data: get_fuel_price() gives None and every caller says "no fuel price data" instead of a dollar figure (never $0.00).
With data the figures are the same as before. Made-up prices in temporary folders only; no real config or data folder."""
import contextlib
import datetime
import io
import os
import re
import sys
import tempfile
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools")
sys.path.insert(0, TOOLS)
sys.path.insert(1, os.path.join(TOOLS, "commands"))

from lib import fuel_common as FC  # noqa: E402
from lib import charging_analyzer as CA  # noqa: E402
import sync_fuel_history as SFH  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
DOLLARS = re.compile(r"\$\s*\d")
NO = FC.NO_FUEL_PRICE


def add_prices(d, rows):
    """rows: (date, fuel_code, cents)"""
    conn = FC.get_db_connection(d)
    conn.executemany("INSERT INTO fuel_daily_averages (date, fuel_type, region, avg_price_cents, sample_count) VALUES (?,?,'all',?,100)",
                     rows)
    conn.commit()
    conn.close()


PRICES = [("2026-06-01", "U91", 201.5), ("2026-06-01", "P98", 229.9), ("2026-06-01", "DL", 199.0)]


def printed(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **k)
    return ANSI.sub("", buf.getvalue())


class GetFuelPriceTest(unittest.TestCase):
    def test_no_data_is_none_for_every_fuel(self):
        d = tempfile.mkdtemp()
        for ft in ("U91", "P95", "P98", "Diesel", "DL", "E10", "LPG"):
            self.assertIsNone(FC.get_fuel_price("2026-06-01", ft, tessie_dir=d), ft)

    def test_one_fuel_held_others_none(self):
        d = tempfile.mkdtemp()
        add_prices(d, [("2026-06-01", "U91", 201.5)])
        self.assertAlmostEqual(FC.get_fuel_price("2026-06-01", "U91", tessie_dir=d), 2.015)
        self.assertIsNone(FC.get_fuel_price("2026-06-01", "Diesel", tessie_dir=d))

    def test_nearest_day_has_no_distance_limit(self):
        # Current behaviour, kept on purpose (see docs/MADE_UP_NUMBERS_2026-11.md section 2): the nearest held day is used however far away.
        d = tempfile.mkdtemp()
        add_prices(d, [("2020-03-06", "U91", 150.0), ("2026-05-20", "U91", 210.0)])
        self.assertAlmostEqual(FC.get_fuel_price("2030-01-01", "U91", tessie_dir=d), 2.10)
        self.assertAlmostEqual(FC.get_fuel_price("2019-01-01", "U91", tessie_dir=d), 1.50)
        self.assertAlmostEqual(FC.get_fuel_price(datetime.datetime(2026, 5, 21, 23, 0), "U91", tessie_dir=d), 2.10)


class FormatHelpersTest(unittest.TestCase):
    def test_price(self):
        self.assertEqual(FC.fmt_price_per_l(None), NO)
        self.assertEqual(FC.fmt_price_per_l(2.015), "$2.02/L")

    def test_cost(self):
        self.assertEqual(FC.fmt_fuel_cost(None, None, 8.0), NO)
        self.assertEqual(FC.fmt_fuel_cost(None, 2.0, None), "unknown (no L/100km)")
        self.assertEqual(FC.fmt_fuel_cost(3.834, 2.0, 8.0, width=5), "$ 3.83")
        self.assertEqual(FC.fmt_fuel_cost(19.9, 1.99, 10.0), "$19.90")


class DriveComparisonTest(unittest.TestCase):
    def lines(self, d):
        return ANSI.sub("", "\n".join(FC.format_drive_fuel_comparison(20.0, "2026-06-01", kwh_used=3.0, duration_mins=30, tessie_dir=d)))

    def test_no_data(self):
        text = self.lines(tempfile.mkdtemp())
        self.assertIn(f"Ref Fuel Prices: {NO}", text)
        self.assertIn(f"1.92 L · {NO}", text)                # Mazda CX-8: litres still shown, no cost
        self.assertIsNone(DOLLARS.search(text), text)
        self.assertNotIn("Saved", text)

    def test_with_data_unchanged(self):
        d = tempfile.mkdtemp()
        add_prices(d, PRICES)
        text = self.lines(d)
        self.assertIn("Ref Fuel Prices: U91: $2.02/L  |  P98: $2.30/L  |  Diesel: $1.99/L", text)
        self.assertIn("1.92 L @ $1.99/L = $ 3.83", text)
        self.assertIn("1.72 L @ $2.30/L = $ 3.96", text)
        self.assertNotIn(NO, text)

    def test_only_u91_held(self):
        d = tempfile.mkdtemp()
        add_prices(d, [("2026-06-01", "U91", 201.5)])
        text = self.lines(d)
        self.assertIn(f"U91: $2.02/L  |  P98: {NO}  |  Diesel: {NO}", text)
        self.assertIn("0.98 L @ $2.02/L = $ 1.97", text)      # RAV4 Hybrid (U91)
        self.assertIn(f"1.92 L · {NO}", text)                 # CX-8 (Diesel)


class SyncFuelHistoryTest(unittest.TestCase):
    def test_query_no_data(self):
        out = printed(SFH.query_price, "2026-06-01", "U91", "all", tempfile.mkdtemp())
        self.assertIn(f"[U91] (all): {NO}", out)
        self.assertIn(f"[Diesel] · {NO}", out)
        self.assertIsNone(DOLLARS.search(out), out)

    def test_query_with_data_unchanged(self):
        d = tempfile.mkdtemp()
        add_prices(d, PRICES)
        out = printed(SFH.query_price, "2026-06-01", "U91", "all", d)
        self.assertIn("[U91] (all): $2.015/L", out)
        self.assertIn("10.0 L/100km [Diesel @ $1.99/L] = $19.90", out)       # Mazda CX-8
        self.assertNotIn(NO, out)

    def test_status_no_data(self):
        out = printed(SFH.print_status, tempfile.mkdtemp())
        self.assertIn(f"Latest Reference Prices:  {NO}", out)
        self.assertIsNone(DOLLARS.search(out), out)

    def test_status_with_data(self):
        d = tempfile.mkdtemp()
        today = datetime.date.today().strftime("%Y-%m-%d")
        add_prices(d, [(today, "U91", 201.5), (today, "P98", 229.9), (today, "DL", 199.0)])
        out = printed(SFH.print_status, d)
        self.assertIn("U91: $2.02/L  |  P98: $2.30/L  |  Diesel: $1.99/L", out)


class ChargingSummaryTest(unittest.TestCase):
    def analyzer(self, d):
        a = CA.ChargingAnalyzer.__new__(CA.ChargingAnalyzer)
        a.tessie_data_home, a.tessie_dirs = d, [d]
        a._load_drive_totals_in_period = lambda s, e: {"distance_km": 150.0, "energy_used_kwh": 24.0}
        return a

    def sessions(self, dated=True):
        dt = datetime.datetime(2026, 6, 1, 10, 0)
        return [dict(battery_kwh=20.0, datetime=dt, invoice_cost=None, tessie_cost=6.0),
                dict(battery_kwh=10.0, datetime=dt if dated else None, invoice_cost=4.0, tessie_cost=0.0)]

    def test_summary_line_no_data(self):
        a = self.analyzer(tempfile.mkdtemp())
        ice = a._ice_equivalent(self.sessions(), 7.5, "Diesel", 160.0, FC.get_fuel_price)
        self.assertIsNone(ice["spend"])
        line = CA.format_ice_equivalent(ice, "Mazda CX-8 (Diesel)", "Diesel", 7.5, None)
        self.assertEqual(line, f"{NO} - Mazda CX-8 (Diesel)")
        self.assertEqual(CA.annual_savings_unknown(7.5, ice), NO)
        self.assertEqual(CA.annual_savings_unknown(None, ice), "unknown (no L/100km)")

    def test_summary_line_with_data_unchanged(self):
        d = tempfile.mkdtemp()
        add_prices(d, PRICES)
        a = self.analyzer(d)
        ice = a._ice_equivalent(self.sessions(), 7.5, "Diesel", 160.0, FC.get_fuel_price)
        # 30 kWh / 160 Wh/km = 187.5 km x 7.5 L/100km x $1.99 = $27.98; EV side $10
        line = ANSI.sub("", CA.format_ice_equivalent(ice, "Mazda CX-8 (Diesel)", "Diesel", 7.5, ice["spend"] - ice["ev_spend"]))
        self.assertEqual(line, "$27.98 (Mazda CX-8: Diesel ~$1.99/L, 7.5 L/100km; car 160 Wh/km)  │  Net Savings: +$17.98 AUD")
        self.assertIsNone(ice["price_reason"])

    def test_charge_without_date(self):
        d = tempfile.mkdtemp()
        add_prices(d, PRICES)
        ice = self.analyzer(d)._ice_equivalent(self.sessions(dated=False), 7.5, "Diesel", 160.0, FC.get_fuel_price)
        self.assertIsNone(ice["spend"])
        self.assertEqual(ice["reason"], "no fuel price for 1 of 2 charges")
        self.assertIn("unknown (no fuel price for 1 of 2 charges)", CA.format_ice_equivalent(ice, "Car", "Diesel", 7.5, None))

    def test_matrix_no_data(self):
        out = printed(self.analyzer(tempfile.mkdtemp())._print_multi_vehicle_comparison_matrix, self.sessions(), 10.0,
                      km_travelled=150.0, period_days=10)
        self.assertIn("no data", out)
        self.assertIn("No fuel price data for Diesel, U91, P98", out)
        self.assertIsNone(DOLLARS.search(out.split("No fuel price data")[0]), out)

    def test_matrix_with_data_unchanged(self):
        d = tempfile.mkdtemp()
        add_prices(d, PRICES)
        out = printed(self.analyzer(d)._print_multi_vehicle_comparison_matrix, self.sessions(), 10.0, km_travelled=150.0, period_days=10)
        row = next(r for r in out.splitlines() if "Mazda CX-8" in r)
        for cell in ("$1.99/L", "$27.98", "+$17.98", "+$452/yr"):
            self.assertIn(cell, row)
        self.assertNotIn("No fuel price data", out)


if __name__ == "__main__":
    unittest.main()
