"""test_supercharger_pricing.py - lib/supercharger_pricing.py: what a Supercharger kWh costs on a date. Real sessions
read from a charges_master.csv, the daytime filter, price regimes (a lone odd session does not start one), the date
the current regime starts, its live average, and the published step history for older dates.

Sessions, rates and dates are made up; the CSV is written to a temporary folder.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_supercharger_pricing.py
"""
import csv
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import supercharger_pricing as SP  # noqa: E402


def s(day, rate, hour=10):
    """A made-up session `day` days after 1 Jan 2026 at `hour`."""
    return datetime(2026, 1, 1, hour) + timedelta(days=day), rate


OLD = [s(0, 0.50), s(5, 0.51), s(10, 0.49)]
NEW = [s(40, 0.62), s(45, 0.63), s(50, 0.61)]
HISTORY = [{"effective_from": date(2025, 1, 1), "rate_per_kwh": 0.45},
           {"effective_from": date(2025, 7, 1), "rate_per_kwh": 0.48}]


class RegimeTest(unittest.TestCase):
    def test_one_regime(self):
        r = SP.detect_real_rate_regimes(OLD)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["effective_from"], r[0]["last_session_at"], r[0]["session_count"]),
                         (date(2026, 1, 1), date(2026, 1, 11), 3))
        self.assertAlmostEqual(r[0]["rate_per_kwh"], 0.50)

    def test_a_price_change_needs_two_sessions_that_agree(self):
        r = SP.detect_real_rate_regimes(OLD + NEW)
        self.assertEqual([x["effective_from"] for x in r], [date(2026, 1, 1), date(2026, 2, 10)])
        self.assertAlmostEqual(r[1]["rate_per_kwh"], 0.62)
        self.assertEqual(r[1]["session_count"], 3)

    def test_a_lone_outlier_is_left_out(self):
        r = SP.detect_real_rate_regimes(OLD[:2] + [s(7, 0.80)] + OLD[2:])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["session_count"], 3)
        self.assertAlmostEqual(r[0]["rate_per_kwh"], 0.50)

    def test_within_tolerance_stays_in_the_regime(self):
        r = SP.detect_real_rate_regimes([s(0, 0.50), s(1, 0.53), s(2, 0.47)], tolerance=0.03)
        self.assertEqual(len(r), 1)

    def test_empty(self):
        self.assertEqual(SP.detect_real_rate_regimes([]), [])


class CurrentRateTest(unittest.TestCase):
    def test_daytime_filter(self):
        sessions = [s(0, 0.50, hour=5), s(0, 0.50, hour=6), s(0, 0.50, hour=17), s(0, 0.50, hour=18)]
        self.assertEqual([dt.hour for dt, _ in SP.daytime_only_sessions(sessions)], [6, 17])

    def test_average_since_and_daytime(self):
        sessions = OLD + NEW + [s(51, 0.30, hour=22)]   # a cheap night session
        rate, n = SP.current_supercharger_rate(real_sessions=sessions, since=date(2026, 2, 1))
        self.assertEqual(n, 3)
        self.assertAlmostEqual(rate, 0.62)
        rate, n = SP.current_supercharger_rate(daytime_only=False, real_sessions=sessions, since=date(2026, 2, 1))
        self.assertEqual(n, 4)
        self.assertEqual(SP.current_supercharger_rate(real_sessions=[]), (None, 0))

    def test_since_is_the_later_of_live_regime_and_history(self):
        self.assertIsNone(SP.resolve_since_date([], HISTORY))
        self.assertEqual(SP.resolve_since_date(OLD + NEW, HISTORY), date(2026, 2, 10))
        later = HISTORY + [{"effective_from": date(2026, 3, 1), "rate_per_kwh": 0.62}]
        self.assertEqual(SP.resolve_since_date(OLD + NEW, later), date(2026, 3, 1))
        self.assertEqual(SP.resolve_since_date(OLD, []), date(2026, 1, 1))

    def test_current_regime_rate_ignores_the_old_regime(self):
        rate, n, since = SP.current_regime_rate(real_sessions=OLD + NEW, published_history=HISTORY)
        self.assertEqual((n, since), (3, date(2026, 2, 10)))
        self.assertAlmostEqual(rate, 0.62)

    def test_rate_for_date(self):
        f = SP.supercharger_rate_for_date
        kw = dict(real_sessions=OLD + NEW, published_history=HISTORY)
        self.assertIsNone(f(date(2024, 12, 31), **kw))
        self.assertEqual(f(date(2025, 3, 1), **kw), 0.45)
        self.assertEqual(f(date(2025, 12, 1), **kw), 0.48)    # published step, before the live regime
        self.assertAlmostEqual(f(date(2026, 2, 10), **kw), 0.62)
        self.assertAlmostEqual(f(date(2026, 6, 1), **kw), 0.62)
        self.assertEqual(f(date(2026, 6, 1), current_rate=0.70, **kw), 0.70)


class LoadSessionsTest(unittest.TestCase):
    def test_reads_only_priced_supercharger_rows_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "charges"))
            rows = [
                {"Supercharger": "true", "Started At (Local)": "2026-03-02 14:00 AEDT", "Cost Per kWh": "0.62"},
                {"Supercharger": "TRUE", "Started At (Local)": "2026-03-01 09:30 AEDT", "Cost Per kWh": "0.60"},
                {"Supercharger": "false", "Started At (Local)": "2026-03-03 10:00 AEDT", "Cost Per kWh": "0.20"},
                {"Supercharger": "true", "Started At (Local)": "", "Cost Per kWh": "0.61"},
                {"Supercharger": "true", "Started At (Local)": "2026-03-04 10:00 AEDT", "Cost Per kWh": "0"},
                {"Supercharger": "true", "Started At (Local)": "2026-03-05 10:00 AEDT", "Cost Per kWh": "n/a"},
                {"Supercharger": "true", "Started At (Local)": "garbage", "Cost Per kWh": "0.61"},
            ]
            with open(os.path.join(d, "charges", "charges_master.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            got = SP.load_real_supercharger_sessions(d)
        self.assertEqual(got, [(datetime(2026, 3, 1, 9, 30), 0.60), (datetime(2026, 3, 2, 14, 0), 0.62)])

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(SP.load_real_supercharger_sessions(d), [])


if __name__ == "__main__":
    unittest.main()
