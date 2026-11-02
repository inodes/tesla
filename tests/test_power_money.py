"""test_power_money.py - the money in the power bill: NSW public holidays and the demand window (lib/agl_common.py and
the AGL provider's own copy), the solar credit tiers, the demand charge, time-of-use blending, the rate period for a
date, and lib/power_bill.py's bill rebuild (GST on charges only, a cycle split at a rate change).

All rates, usage and dates are made up. No config, tariff file or usage database is read: the provider's file
reads (gst_multiplier, load_rate_periods, the usage database) are replaced by fakes.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_power_money.py
"""
import os
import sys
import unittest
from datetime import date, datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import agl_common as AC  # noqa: E402
from lib import power_bill as PB  # noqa: E402
from providers.power.power_agl import AGLProvider, TIER1_CAP_KWH_PER_DAY, TIER2_RATE  # noqa: E402


def period(eff_from, eff_to, **kw):
    p = dict(effective_from=eff_from.isoformat(), _effective_from_date=eff_from, _effective_to_date=eff_to,
             usage_rate_per_kwh=0.30, supply_charge_per_day=1.00, solar_feed_in_tier1_rate_per_kwh=0.10,
             demand_rate_per_kw_day=0.20)
    p.update(kw)
    return p


class HolidaysTest(unittest.TestCase):
    """The same calendar lives in lib/agl_common.py and providers/power/power_agl.py; both are checked."""

    CALENDARS = (("agl_common", AC.nsw_public_holidays), ("AGLProvider", AGLProvider.nsw_public_holidays))

    def test_easter(self):
        self.assertEqual(AC._easter_sunday(2025), date(2025, 4, 20))
        self.assertEqual(AC._easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(AC._easter_sunday(2027), date(2027, 3, 28))

    def test_2026_calendar(self):
        expected = {date(2026, 1, 1), date(2026, 1, 26), date(2026, 4, 3), date(2026, 4, 4), date(2026, 4, 6),
                    date(2026, 4, 25), date(2026, 6, 8), date(2026, 10, 5), date(2026, 12, 25), date(2026, 12, 26),
                    date(2026, 12, 28)}  # Boxing Day 2026 is a Saturday -> Monday 28th
        for name, cal in self.CALENDARS:
            with self.subTest(name):
                self.assertEqual(cal(2026), expected)

    def test_weekend_new_year_and_australia_day_move_to_monday(self):
        for name, cal in self.CALENDARS:
            with self.subTest(name):
                self.assertIn(date(2025, 1, 27), cal(2025))   # Australia Day 2025 is a Sunday
                self.assertIn(date(2022, 1, 3), cal(2022))    # New Year's Day 2022 is a Saturday

    def test_christmas_saturday_boxing_day_sunday(self):
        """Was a bug (found by this test, fixed 2026-11-02): when Christmas falls on a Saturday (2021, 2027) NSW adds Monday 27th for
        Christmas and Tuesday 28th for Boxing Day. Both substitutes are computed as 'the next Monday', so the
        Tuesday is lost, and a weekday afternoon in the summer demand window is wrongly counted toward demand."""
        for name, cal in self.CALENDARS:
            with self.subTest(name):
                self.assertTrue({date(2027, 12, 27), date(2027, 12, 28)} <= cal(2027))

    def test_christmas_sunday(self):
        """Was a bug (found by this test, fixed 2026-11-02): Christmas on a Sunday (2022): Boxing Day is Monday 26th, so Christmas's own
        substitute is Tuesday 27th. The code gives Monday 26th twice and misses the 27th."""
        for name, cal in self.CALENDARS:
            with self.subTest(name):
                self.assertIn(date(2022, 12, 27), cal(2022))


class DemandWindowTest(unittest.TestCase):
    CHECKS = [
        (datetime(2026, 7, 14, 16, 30), False),  # winter weekday, before 5pm
        (datetime(2026, 7, 14, 17, 0), True),    # winter weekday, 5pm
        (datetime(2026, 7, 14, 20, 30), True),
        (datetime(2026, 7, 14, 21, 0), False),   # window ends at 9pm
        (datetime(2026, 2, 10, 14, 0), True),    # summer weekday 2pm
        (datetime(2026, 2, 10, 19, 30), True),
        (datetime(2026, 2, 10, 20, 0), False),   # window ends at 8pm
        (datetime(2026, 2, 10, 13, 30), False),
        (datetime(2026, 7, 18, 18, 0), False),   # Saturday
        (datetime(2026, 5, 12, 18, 0), False),   # shoulder month
        (datetime(2026, 10, 13, 15, 0), False),  # shoulder month
        (datetime(2026, 6, 8, 18, 0), False),    # King's Birthday holiday
        (datetime(2026, 1, 26, 15, 0), False),   # Australia Day
    ]

    def test_window(self):
        prov = AGLProvider()
        for dt, want in self.CHECKS:
            with self.subTest(dt=dt):
                self.assertEqual(AC.in_demand_window(dt), want)
                self.assertEqual(prov.in_demand_window(dt), want)


class ProviderMoneyTest(unittest.TestCase):
    def setUp(self):
        self.p = AGLProvider()
        g = mock.patch.object(AGLProvider, "gst_multiplier", return_value=1.10)
        g.start()
        self.addCleanup(g.stop)

    def test_solar_credit_tiers(self):
        # 30 days: tier 1 covers TIER1_CAP_KWH_PER_DAY * 30 kWh, the rest earns TIER2_RATE
        cap = TIER1_CAP_KWH_PER_DAY * 30
        self.assertEqual(self.p.solar_credit(cap - 50, 30, 0.10), (round((cap - 50) * 0.10, 2), 0.0))
        self.assertEqual(self.p.solar_credit(cap + 100, 30, 0.10), (round(cap * 0.10, 2), round(100 * TIER2_RATE, 2)))
        self.assertEqual(self.p.solar_credit(0, 30, 0.10), (0.0, 0.0))

    def test_solar_tier_split_for_one_session(self):
        # 8 kWh already exported today, a 5 kWh session: 2 kWh left in tier 1, 3 kWh in tier 2
        t1, t2, r2 = self.p.solar_tier_split_kwh(TIER1_CAP_KWH_PER_DAY - 2, 5)
        self.assertEqual((t1, t2, r2), (2, 3, TIER2_RATE))
        t1, t2, _ = self.p.solar_tier_split_kwh(TIER1_CAP_KWH_PER_DAY + 5, 4)
        self.assertEqual((t1, t2), (0, 4))

    def test_demand_charge(self):
        self.assertEqual(self.p.demand_charge(3.0, 30, 0.25), (22.5, 24.75))

    def test_flat_rate(self):
        r, br = self.p.usage_rate_for_window(period(date(2026, 1, 1), date(2026, 12, 31)),
                                             datetime(2026, 3, 1, 10), datetime(2026, 3, 1, 11))
        self.assertEqual((r, br), (0.30, {"flat_rate": 0.30}))

    def test_tou_straddling_peak_start(self):
        tou = period(date(2026, 1, 1), date(2026, 12, 31), tariff_type="tou", usage_rate_per_kwh=None,
                     peak_months=[1, 2, 3], peak_hour_start=15, peak_hour_end=21,
                     peak_rate_per_kwh=0.50, offpeak_rate_per_kwh=0.20)
        # 14:30-15:30 in a peak month: half off-peak, half peak
        r, br = self.p.usage_rate_for_window(tou, datetime(2026, 2, 3, 14, 30), datetime(2026, 2, 3, 15, 30))
        self.assertAlmostEqual(br["peak_fraction"], 0.5)
        self.assertAlmostEqual(r, 0.35)
        # same hours outside the peak months: all off-peak
        r, _ = self.p.usage_rate_for_window(tou, datetime(2026, 5, 5, 14, 30), datetime(2026, 5, 5, 15, 30))
        self.assertAlmostEqual(r, 0.20)
        # zero-length window takes the rate of its start minute
        r, _ = self.p.usage_rate_for_window(tou, datetime(2026, 2, 3, 16), datetime(2026, 2, 3, 16))
        self.assertEqual(r, 0.50)

    def test_tou3_weekday_weekend_and_holiday(self):
        tou3 = period(date(2026, 1, 1), date(2026, 12, 31), tariff_type="tou3", usage_rate_per_kwh=None,
                      peak_hour_start=14, peak_hour_end=20, shoulder_weekday_windows=[[7, 14], [20, 22]],
                      shoulder_weekend_hour_start=7, shoulder_weekend_hour_end=22,
                      peak_rate_per_kwh=0.60, shoulder_rate_per_kwh=0.30, offpeak_rate_per_kwh=0.15)
        u = self.p.usage_rate_for_window
        self.assertAlmostEqual(u(tou3, datetime(2026, 7, 14, 15), datetime(2026, 7, 14, 16))[0], 0.60)   # weekday peak
        self.assertAlmostEqual(u(tou3, datetime(2026, 7, 14, 8), datetime(2026, 7, 14, 9))[0], 0.30)     # weekday shoulder
        self.assertAlmostEqual(u(tou3, datetime(2026, 7, 14, 23), datetime(2026, 7, 15, 0))[0], 0.15)    # night
        self.assertAlmostEqual(u(tou3, datetime(2026, 7, 18, 15), datetime(2026, 7, 18, 16))[0], 0.30)   # Saturday
        self.assertAlmostEqual(u(tou3, datetime(2026, 6, 8, 15), datetime(2026, 6, 8, 16))[0], 0.30)     # holiday Monday
        # 13:30-14:30 weekday: half shoulder, half peak
        self.assertAlmostEqual(u(tou3, datetime(2026, 7, 14, 13, 30), datetime(2026, 7, 14, 14, 30))[0], 0.45)

    def test_rate_for_date(self):
        a = period(date(2025, 7, 1), date(2026, 6, 30), usage_rate_per_kwh=0.30)
        b = period(date(2026, 7, 1), date(2026, 9, 30), usage_rate_per_kwh=0.33)
        self.assertIs(self.p.rate_for_date(date(2026, 6, 30), [a, b]), a)
        self.assertIs(self.p.rate_for_date(date(2026, 7, 1), [a, b]), b)
        self.assertIs(self.p.rate_for_date(date(2027, 1, 1), [a, b]), b)   # past the open-ended last period
        with self.assertRaises(ValueError):
            self.p.rate_for_date(date(2024, 1, 1), [a, b])


class PowerBillTest(unittest.TestCase):
    def test_daterange_days_is_inclusive(self):
        self.assertEqual(PB.daterange_days(date(2026, 4, 25), date(2026, 5, 24)), 30)
        self.assertEqual(PB.daterange_days(date(2026, 5, 1), date(2026, 5, 1)), 1)

    def test_demand_month_is_the_month_before_the_cycle_end(self):
        self.assertEqual(PB.demand_month_for_cycle_end(date(2026, 5, 24)), (2026, 4))
        self.assertEqual(PB.demand_month_for_cycle_end(date(2026, 1, 10)), (2025, 12))

    def test_missing_rate_fields(self):
        self.assertEqual(PB._missing_rate_fields(period(date(2026, 1, 1), date(2026, 1, 31))), [])
        self.assertEqual(PB._missing_rate_fields(period(date(2026, 1, 1), date(2026, 1, 31), usage_rate_per_kwh=None)),
                         ["usage_rate_per_kwh"])
        tou = period(date(2026, 1, 1), date(2026, 1, 31), tariff_type="tou", usage_rate_per_kwh=None,
                     peak_rate_per_kwh=0.5, offpeak_rate_per_kwh=None, supply_charge_per_day=None)
        self.assertEqual(PB._missing_rate_fields(tou), ["supply_charge_per_day", "offpeak_rate_per_kwh"])
        tou3 = period(date(2026, 1, 1), date(2026, 1, 31), tariff_type="tou3", usage_rate_per_kwh=None,
                      peak_rate_per_kwh=0.5, shoulder_rate_per_kwh=0.3, offpeak_rate_per_kwh=0.1)
        self.assertEqual(PB._missing_rate_fields(tou3), [])

    def _periods(self):
        return [period(date(2026, 1, 1), date(2026, 3, 9), usage_rate_per_kwh=0.30, supply_charge_per_day=1.00),
                period(date(2026, 3, 10), date(2026, 12, 31), usage_rate_per_kwh=0.40, supply_charge_per_day=1.20)]

    def _rate_for_date(self, d, periods=None):
        return AGLProvider().rate_for_date(d, periods or self._periods())

    def test_split_at_a_rate_change(self):
        with mock.patch.object(PB, "rate_for_date", self._rate_for_date):
            parts = PB.split_by_rate_period(date(2026, 3, 1), date(2026, 3, 20), self._periods())
        self.assertEqual([(a, b, p["usage_rate_per_kwh"]) for a, b, p in parts],
                         [(date(2026, 3, 1), date(2026, 3, 9), 0.30), (date(2026, 3, 10), date(2026, 3, 20), 0.40)])
        with mock.patch.object(PB, "rate_for_date", self._rate_for_date):
            parts = PB.split_by_rate_period(date(2026, 4, 1), date(2026, 4, 30), self._periods())
        self.assertEqual(len(parts), 1)

    def _rebuild(self, periods, import_kwh_per_day=10.0, solar_kwh_per_day=5.0, latched=(2.0, datetime(2026, 2, 3, 17))):
        prov = AGLProvider()

        def usage(_home, s, e, per, _prov):
            kwh = import_kwh_per_day * PB.daterange_days(s, e)
            return kwh * per["usage_rate_per_kwh"], kwh

        def solar(_home, role, s, e, _prov=None):
            self.assertEqual(role, "solar_export")
            return solar_kwh_per_day * PB.daterange_days(s, e)

        with mock.patch.object(PB, "get_power_provider", return_value=prov), \
                mock.patch.object(AGLProvider, "gst_multiplier", return_value=1.10), \
                mock.patch.object(PB, "rate_for_date", self._rate_for_date), \
                mock.patch.object(PB, "usage_cost_for_range", usage), \
                mock.patch.object(PB, "sum_kwh", solar), \
                mock.patch.object(PB, "monthly_latched_kw", return_value=latched), \
                mock.patch.object(PB, "demand_charge", lambda kw, days, rate: (round(kw * days * rate, 2), None)):
            return PB.reconstruct_bill(date(2026, 3, 1), date(2026, 3, 20), data_home="/nonexistent", periods=periods)

    def test_bill_rebuild_across_a_rate_change(self):
        bill = self._rebuild(self._periods())
        # 9 days at 0.30 and 11 days at 0.40, 10 kWh/day
        self.assertEqual(bill["days"], 20)
        self.assertAlmostEqual(bill["usage_ex_gst"], 9 * 10 * 0.30 + 11 * 10 * 0.40)
        self.assertAlmostEqual(bill["supply_ex_gst"], 9 * 1.00 + 11 * 1.20)
        # 5 kWh/day of export stays under the 10 kWh/day tier-1 cap: all at 0.10
        self.assertAlmostEqual(bill["solar_credit_ex_gst"], 20 * 5 * 0.10)
        self.assertAlmostEqual(bill["demand_ex_gst"], 2.0 * 20 * 0.20)
        self.assertEqual(bill["demand_month"], "2026-02")
        charges = bill["usage_ex_gst"] + bill["supply_ex_gst"] + bill["demand_ex_gst"]
        self.assertAlmostEqual(bill["total_ex_gst"], round(charges - bill["solar_credit_ex_gst"], 2))
        # GST is on the charges only, never netted against the solar credit
        self.assertAlmostEqual(bill["total_incl_gst"], round(charges * 1.10 - bill["solar_credit_ex_gst"], 2))

    def test_no_demand_in_a_shoulder_month(self):
        bill = self._rebuild(self._periods(), latched=(0.0, None))
        self.assertEqual(bill["demand_ex_gst"], 0.0)

    def test_incomplete_period_is_a_clear_error(self):
        periods = self._periods()
        periods[1]["supply_charge_per_day"] = None
        with self.assertRaises(ValueError) as cm:
            self._rebuild(periods)
        self.assertIn("supply_charge_per_day", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
