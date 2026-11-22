"""pv_history_db.intraday_days_present looks only at the days asked for (the timestamp is the key)."""
import os
import sqlite3
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
import pv_history_db as P  # noqa: E402


class DaysPresentTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.execute("CREATE TABLE pv_intraday (timestamp TEXT PRIMARY KEY, production_kwh REAL)")
        for d in ("2026-11-19", "2026-11-20", "2026-11-22", "2026-11-23"):
            for hm in ("00:00", "12:15", "23:45"):
                self.c.execute("INSERT INTO pv_intraday VALUES (?, 1)", (d + " " + hm + ":00",))

    def test_only_the_days_in_the_range(self):
        self.assertEqual(P.intraday_days_present(self.c, date(2026, 11, 20), date(2026, 11, 22)), {"2026-11-20", "2026-11-22"})

    def test_without_a_range_every_day(self):
        self.assertEqual(len(P.intraday_days_present(self.c)), 4)


if __name__ == "__main__":
    unittest.main()
