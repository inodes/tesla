#!/usr/bin/env python3
"""Every fuel price says which date it is from when it is not the date asked: get_fuel_price_dated() (day / month / nearest), the note
fmt_price_basis() adds, and the refresh an out-of-date price triggers first (mocked: no network). Made-up prices in temporary folders only."""
import contextlib
import datetime
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools")
sys.path.insert(0, TOOLS)
sys.path.insert(1, os.path.join(TOOLS, "commands"))

from lib import fuel_common as FC  # noqa: E402
from lib import charging_analyzer as CA  # noqa: E402
import sync_fuel_history as SFH  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def make_dir(daily=(), monthly=(), synced=True):
    """daily: (date, fuel_code, cents); monthly: (YYYY-MM, fuel_code, cents). synced: a month in the sync log (prices set up by the sync)."""
    d = tempfile.mkdtemp()
    conn = FC.get_db_connection(d)
    conn.executemany("INSERT INTO fuel_daily_averages (date, fuel_type, region, avg_price_cents, sample_count) VALUES (?,?,'all',?,100)", daily)
    conn.executemany("INSERT INTO fuel_monthly_averages (year_month, fuel_type, region, avg_price_cents, sample_count) VALUES (?,?,'all',?,100)", monthly)
    if synced:
        conn.execute("INSERT INTO fuel_sync_log (resource_id, month_year, status) VALUES ('r1', '2026-05', 'synced')")
    conn.commit()
    conn.close()
    return d


def printed(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **k)
    return ANSI.sub("", buf.getvalue())


class Base(unittest.TestCase):
    def setUp(self):
        FC._REFRESH["tried"] = False
        self.fetch = mock.Mock(return_value=False)
        p = mock.patch.object(FC, "_FETCH", self.fetch)
        p.start()
        self.addCleanup(p.stop)
        err = contextlib.redirect_stderr(io.StringIO())
        self.stderr = err.__enter__()
        self.addCleanup(err.__exit__, None, None, None)


class DatedLookupTest(Base):
    def test_day(self):
        d = make_dir([("2026-05-20", "U91", 210.0)])
        self.assertEqual(FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d), dict(price=2.10, price_date="2026-05-20", basis="day"))

    def test_month(self):
        d = make_dir([("2026-05-20", "U91", 210.0)], [("2026-04", "U91", 199.0)])
        self.assertEqual(FC.get_fuel_price_dated("2026-04-02", "U91", tessie_dir=d), dict(price=1.99, price_date="2026-04", basis="month"))

    def test_nearest(self):
        d = make_dir([("2020-03-06", "U91", 150.0), ("2026-05-20", "U91", 210.0)])
        self.assertEqual(FC.get_fuel_price_dated("2019-01-01", "U91", tessie_dir=d), dict(price=1.50, price_date="2020-03-06", basis="nearest"))

    def test_2030_uses_the_newest_price_with_its_real_date(self):
        d = make_dir([("2020-03-06", "U91", 150.0), ("2026-05-20", "U91", 210.0)])
        self.assertEqual(FC.get_fuel_price_dated("2030-01-01", "U91", tessie_dir=d), dict(price=2.10, price_date="2026-05-20", basis="nearest"))
        self.assertEqual(FC.fmt_price_basis("2030-01-01", FC.get_fuel_price_dated("2030-01-01", "U91", tessie_dir=d)), "(price of 2026-05-20)")

    def test_unreadable_date_is_none_never_the_first_row(self):
        d = make_dir([("2020-03-06", "U91", 150.0), ("2026-05-20", "U91", 210.0)])
        for bad in ("", None, "garbage", "None"):
            self.assertIsNone(FC.get_fuel_price_dated(bad, "U91", tessie_dir=d), repr(bad))
            self.assertIsNone(FC.get_fuel_price(bad, "U91", tessie_dir=d), repr(bad))

    def test_get_fuel_price_is_still_the_float(self):
        d = make_dir([("2026-05-20", "U91", 210.0)])
        self.assertAlmostEqual(FC.get_fuel_price("2026-05-20", "U91", tessie_dir=d), 2.10)
        self.assertAlmostEqual(FC.get_fuel_price(datetime.datetime(2026, 5, 20, 23, 0), "U91", tessie_dir=d), 2.10)
        self.assertIsNone(FC.get_fuel_price("2026-05-20", "Diesel", tessie_dir=d))


class NoteTest(Base):
    def test_no_note_for_day_or_its_month(self):
        self.assertEqual(FC.fmt_price_basis("2026-05-20", dict(price=2.1, price_date="2026-05-20", basis="day")), "")
        self.assertEqual(FC.fmt_price_basis("2026-04-02", dict(price=2.1, price_date="2026-04", basis="month")), "")

    def test_no_note_within_stale_days(self):
        self.assertEqual(FC.fmt_price_basis("2026-05-20", dict(price=2.1, price_date="2026-05-13", basis="nearest")), "")

    def test_note_for_nearest_and_other_month(self):
        self.assertEqual(FC.fmt_price_basis("2026-05-20", dict(price=2.1, price_date="2026-05-12", basis="nearest")), "(price of 2026-05-12)")
        self.assertEqual(FC.fmt_price_basis("2026-05-03", dict(price=2.1, price_date="2026-04", basis="month")), "(price of month 2026-04)")

    def test_future_date_measured_from_today(self):
        today = datetime.date(2026, 11, 4)
        info = dict(price=2.1, price_date="2026-11-01", basis="nearest")
        self.assertEqual(FC.fmt_price_basis("2030-01-01", info, today=today), "")
        self.assertEqual(FC.fmt_price_basis("2030-01-01", dict(info, price_date="2026-10-01"), today=today), "(price of 2026-10-01)")

    def test_stale_days_is_the_chart_limit(self):
        import price_history_report as PHR
        self.assertEqual(FC.fuel_stale_days(), PHR.MAX_FILL_DAYS)


class RefreshTest(Base):
    def test_current_price_no_refresh_no_note(self):
        d = make_dir([("2026-05-15", "U91", 210.0)])
        info = FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        self.assertEqual(FC.fmt_price_basis("2026-05-20", info), "")
        self.fetch.assert_not_called()

    def test_8_days_old_refreshes_once_and_notes(self):
        d = make_dir([("2026-05-12", "U91", 210.0)])
        info = FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        self.assertEqual(FC.fmt_price_basis("2026-05-20", info), "(price of 2026-05-12)")
        self.fetch.assert_called_once_with(d)
        self.assertIn("Refreshing fuel prices...", self.stderr.getvalue())
        FC.get_fuel_price_dated("2026-05-20", "P98", tessie_dir=d)
        self.assertEqual(self.fetch.call_count, 1)          # one attempt per run
        state = json.load(open(os.path.join(d, "fuel", "refresh_state.json"), encoding="utf-8"))
        self.assertIn("last_attempt_epoch", state)

    def test_no_second_refresh_within_refetch_hours(self):
        d = make_dir([("2026-05-12", "U91", 210.0)])
        FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        FC._REFRESH["tried"] = False                         # a new run
        FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        self.assertEqual(self.fetch.call_count, 1)

    def test_refresh_that_helps_removes_the_note(self):
        d = make_dir([("2026-05-12", "U91", 210.0)])

        def fetch(td):
            conn = FC.get_db_connection(td)
            conn.execute("INSERT INTO fuel_daily_averages (date, fuel_type, region, avg_price_cents, sample_count) VALUES ('2026-05-20','U91','all',220.0,100)")
            conn.commit()
            conn.close()
            return True
        self.fetch.side_effect = fetch
        info = FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        self.assertEqual(info, dict(price=2.20, price_date="2026-05-20", basis="day"))
        self.assertEqual(FC.fmt_price_basis("2026-05-20", info), "")

    def test_failed_refresh_does_not_raise(self):
        d = make_dir([("2026-05-12", "U91", 210.0)])
        self.fetch.side_effect = OSError("no network")
        info = FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        self.assertEqual(info["price_date"], "2026-05-12")
        self.fetch.assert_called_once()

    def test_future_date_does_not_refresh(self):
        d = make_dir([("2026-05-12", "U91", 210.0)])
        info = FC.get_fuel_price_dated("2030-01-01", "U91", tessie_dir=d)
        self.assertEqual(info["price_date"], "2026-05-12")
        self.fetch.assert_not_called()

    def test_not_set_up_by_the_sync_does_not_refresh(self):
        d = make_dir([("2026-05-12", "U91", 210.0)], synced=False)
        FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        self.fetch.assert_not_called()

    def test_inside_the_sync_does_not_refresh(self):
        d = make_dir([("2026-05-12", "U91", 210.0)])
        with mock.patch.dict(os.environ, {FC.REFRESH_ENV: "1"}):
            FC.get_fuel_price_dated("2026-05-20", "U91", tessie_dir=d)
        self.fetch.assert_not_called()


class PrintedTest(Base):
    SAME_DAY = [("2026-06-01", "U91", 201.5), ("2026-06-01", "P98", 229.9), ("2026-06-01", "DL", 199.0)]

    def drive(self, d, date="2026-06-01"):
        return ANSI.sub("", "\n".join(FC.format_drive_fuel_comparison(20.0, date, kwh_used=3.0, duration_mins=30, tessie_dir=d)))

    def test_same_day_drive_block_unchanged(self):
        out = self.drive(make_dir(self.SAME_DAY))
        refs = f"Ref Fuel Prices: U91: ${2.015:.2f}/L  |  P98: ${2.299:.2f}/L  |  Diesel: ${1.99:.2f}/L"
        self.assertIn(refs + "\n", out + "\n")
        self.assertRegex(out, r" L @ \$\d\.\d\d/L = \$")
        self.assertNotIn("(price of", out)

    def test_old_drive_prices_show_their_date(self):
        out = self.drive(make_dir([(r[0].replace("06-01", "05-01"),) + r[1:] for r in self.SAME_DAY]))
        self.assertIn("|  Diesel: $1.99/L (price of 2026-05-01)", out)
        self.assertRegex(out, r"/L \(price of 2026-05-01\) = \$")

    def test_query_same_day_unchanged_and_old_noted(self):
        d = make_dir(self.SAME_DAY)
        out = printed(SFH.query_price, "2026-06-01", "U91", "all", d)
        self.assertIn("Reference Fuel Price for 2026-06-01 [U91] (all): $2.015/L\n", out)
        self.assertNotIn("(price of", out)
        out = printed(SFH.query_price, "2030-01-01", "U91", "all", d)
        self.assertIn("(all): $2.015/L (price of 2026-06-01)\n", out)
        self.assertIn("/L (price of 2026-06-01)] = $", out)

    def test_status_dates_only_an_old_price(self):
        today = datetime.date.today().isoformat()
        out = printed(SFH.print_status, make_dir([(today, "U91", 201.5)]))
        self.assertIn("Latest Reference Prices:  U91: $", out)
        self.assertNotIn("(price of", out)
        out = printed(SFH.print_status, make_dir([("2026-05-01", "U91", 201.5), ("2026-05-01", "DL", 199.0)]))
        self.assertIn("Diesel: $1.99/L (price of 2026-05-01)", out)

    def test_charging_oldest_out_of_date_price(self):
        d = make_dir([("2026-05-01", "DL", 190.0), ("2026-05-25", "DL", 200.0)])
        a = CA.ChargingAnalyzer.__new__(CA.ChargingAnalyzer)
        a.tessie_data_home, a.tessie_dirs = d, [d]
        sessions = [dict(battery_kwh=20.0, datetime=datetime.datetime(2026, 5, 25, 10), invoice_cost=5.0),
                    dict(battery_kwh=20.0, datetime=datetime.datetime(2026, 5, 12, 10), invoice_cost=5.0),
                    dict(battery_kwh=20.0, datetime=datetime.datetime(2026, 5, 10, 10), invoice_cost=5.0)]
        ice = a._ice_equivalent(sessions, 7.5, "Diesel", 160.0, FC.get_fuel_price, price_dated=FC.get_fuel_price_dated)
        self.assertEqual(ice["price_note"], "(price of 2026-05-01)")
        self.assertIn("~$1.93/L (price of 2026-05-01), 7.5 L/100km", ANSI.sub("", CA.format_ice_equivalent(ice, "Car", "Diesel", 7.5, 1.0)))

    def test_charging_same_day_no_note(self):
        d = make_dir([("2026-05-25", "DL", 200.0)])
        a = CA.ChargingAnalyzer.__new__(CA.ChargingAnalyzer)
        a.tessie_data_home, a.tessie_dirs = d, [d]
        sessions = [dict(battery_kwh=20.0, datetime=datetime.datetime(2026, 5, 25, 10), invoice_cost=5.0)]
        ice = a._ice_equivalent(sessions, 7.5, "Diesel", 160.0, FC.get_fuel_price, price_dated=FC.get_fuel_price_dated)
        self.assertEqual(ice["price_note"], "")
        self.assertIn("~$2.00/L, 7.5 L/100km", ANSI.sub("", CA.format_ice_equivalent(ice, "Car", "Diesel", 7.5, 1.0)))


if __name__ == "__main__":
    unittest.main()
