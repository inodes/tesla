"""fuel_auto: the app fetches last month's FuelCheck prices when it does not hold them yet, and only where fuel prices are set up."""
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import fuel_auto as FA  # noqa: E402


class FuelAutoTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def db(self, last):
        os.makedirs(os.path.join(self.d, "fuel"), exist_ok=True)
        con = sqlite3.connect(os.path.join(self.d, "fuel", "fuel_history.db"))
        con.execute("CREATE TABLE fuel_daily_averages (date TEXT, fuel_type TEXT, region TEXT, avg_price_cents REAL, sample_count INTEGER)")
        con.execute("INSERT INTO fuel_daily_averages VALUES (?, 'U91', 'all', 200, 50)", (last,))
        con.commit()
        con.close()

    def test_no_fuel_database_never_due(self):
        self.assertFalse(FA.due(self.d, datetime.date(2026, 11, 2)))

    def test_last_month_missing_is_due(self):
        self.db("2026-09-30")
        self.assertEqual(FA.latest(self.d), "2026-09-30")
        self.assertTrue(FA.due(self.d, datetime.date(2026, 11, 2)))

    def test_last_month_held_is_not_due(self):
        self.db("2026-10-31")
        self.assertFalse(FA.due(self.d, datetime.date(2026, 11, 2)))
        self.assertTrue(FA.due(self.d, datetime.date(2026, 12, 1)))


if __name__ == "__main__":
    unittest.main()
