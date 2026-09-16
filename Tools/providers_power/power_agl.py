"""
power_agl.py - AGLProvider: AGL (retailer) + Ausgrid (distributor)
billing rules, as a BasePowerProvider implementation.

REQ-070 (2026-09-15): this is every bit of logic that used to live
directly in agl_common.py/agl_usage_sync.py/agl_demand_calculator.py/
agl_cost_calculator.py, moved here UNCHANGED (same NSW public-holiday
calendar, same season-aware Ausgrid EA025 demand window, same pooled
solar-credit tiering, same GST handling) so the generic power_*.py CLI
scripts in Tools/ never hardcode AGL's own rules - AGL is just the first,
and so far only, real implementation of BasePowerProvider. See
power_base.py's own docstring for why the retailer (AGL) and distributor
(Ausgrid) rules are kept together in one class for now.

Design notes preserved from the original files (still true here):

Demand window (AGENTS.md REQ-031): AGL's own invoice PRINTS the demand
window as "2pm-8pm on working weekdays" year-round, but the actual
CHARGED figure only reproduces correctly under a season-aware window -
confirmed by reconciling real half-hourly usage data against real
invoiced kW figures for winter months (see power_demand_calculator.py's
self-test). The printed label on the bill is not trustworthy; the window
logic here is derived from what the bill actually CHARGES.

Solar credit tiering (AGENTS.md REQ-031 - found by reading the FULL
invoice text, not just the headline "Standard feed-in tariff*" line): AGL
pools total solar export across the whole rate sub-period and splits it
into "Standard feed-in tariff*" (up to 10 kWh/day x days-in-period, at
the period's tier-1 rate) and "Next*" (everything beyond that, at a flat
$0.04/kWh that hasn't been observed to change). This is a POOLED
period-total cap, not a per-day cap summed afterward - confirmed because
every observed tier-1 amount is an exact multiple of 10 x the sub-period's
day count, which would be a remarkable coincidence under day-by-day
capping.
"""

import os
from datetime import date, datetime, timedelta

from .power_base import BasePowerProvider, MASTER_FIELDS, load_rate_periods_from_file

TIER2_RATE = 0.04  # flat $0.04/kWh beyond the daily cap - unchanged across every period observed so far
TIER1_CAP_KWH_PER_DAY = 10

REGISTER_ROLE = {
    "E1": "import",
    "B1": "solar_export",
    "E2": "controlled_load",
}


class AGLProvider(BasePowerProvider):
    name = "agl"
    display_name = "AGL"
    # REQ-071: AGL offers no usage-history API for residential accounts
    # (see sync_power_usage.py's module docstring) - every usage-export
    # refresh is a manual CSV download, so this is the provider this
    # flag exists for.
    manual_usage_sync = True

    # ---- paths -------------------------------------------------------

    def config_directory_keys(self):
        return ("power_directory", "agl_directory")

    def config_invoices_directory_keys(self):
        return ("power_invoices_directory", "agl_invoices_directory")

    def default_data_home_name(self):
        return "AGL"

    def default_invoices_home_name(self):
        return "AGL"

    def usage_master_filename(self):
        return "agl_usage_master.csv"

    def tariff_rates_filename(self):
        return "agl_tariff_rates.json"

    def tariff_rates_example_path(self):
        return "AGL/agl_tariff_rates.example.json"

    # ---- usage-export ingestion --------------------------------------

    def is_usage_export_filename(self, filename):
        f_lower = filename.lower()
        return f_lower.endswith(".csv") and "usage" in f_lower

    def parse_datetime(self, s):
        """AGL exports use DD/MM/YYYY H:MM:SS AM/PM (no leading zero on
        hour) consistently across every export format seen so far."""
        return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M:%S %p")

    def register_role(self, register_code):
        """'...#E1' -> 'import', etc. Unknown suffixes pass through raw
        so a future register type shows up visibly instead of being
        dropped."""
        suffix = register_code.rsplit("#", 1)[-1]
        return REGISTER_ROLE.get(suffix, suffix)

    def load_source_rows(self, csv_path):
        import csv

        rows = {}
        skipped = 0
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    start = row["StartDate"].strip()
                    end = row["EndDate"].strip()
                    reg = row["RegisterCode"].strip()
                    kwh = float(row["ProfileReadValue"])
                    # Validate the timestamp parses even though we store
                    # the original string - a row we can't date-parse
                    # isn't safe to trust for anything downstream.
                    self.parse_datetime(start)
                except Exception:
                    skipped += 1
                    continue
                key = (start, end, reg)
                rows[key] = {
                    "StartDate": start,
                    "EndDate": end,
                    "RegisterCode": reg,
                    "Role": self.register_role(reg),
                    "kWh": f"{kwh:.4f}",
                    "SourceFile": os.path.basename(csv_path),
                }
        return rows, skipped

    # ---- NSW public holidays (Ausgrid/EA025 demand-window input) ----
    # Computed, not hardcoded per-year - covers any year in the usage
    # data without needing an annual update. Weekend-substitution (a
    # fixed-date holiday moves to the following Monday when it falls on
    # a Sat/Sun) is applied only to the holidays where it's well-
    # established NSW practice: New Year's Day, Australia Day, Christmas
    # Day, Boxing Day. Anzac Day and Labour Day are included for
    # completeness but never affect the demand calculation below
    # (neither falls inside the Jun-Aug or Nov-Mar windows). This is a
    # best-effort reconstruction of the NSW statutory calendar, not
    # sourced from an official feed.

    _holiday_cache = {}

    @staticmethod
    def _easter_sunday(year):
        """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    @staticmethod
    def _nth_weekday(year, month, weekday, n):
        """The date of the n-th occurrence (1-indexed) of `weekday`
        (0=Mon) in `year`/`month`."""
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        d = d + timedelta(days=offset + 7 * (n - 1))
        return d

    @staticmethod
    def _with_weekend_substitute(d):
        """A fixed-date holiday that falls on Sat/Sun also produces a
        substitute public holiday on the following Monday. Returns the
        set of actual public-holiday dates this one date produces (1 or
        2)."""
        if d.weekday() == 5:  # Saturday
            return {d, d + timedelta(days=2)}
        if d.weekday() == 6:  # Sunday
            return {d, d + timedelta(days=1)}
        return {d}

    @classmethod
    def nsw_public_holidays(cls, year):
        """Set of date() objects - NSW statutory public holidays for
     `year`."""
        holidays = set()
        holidays |= cls._with_weekend_substitute(date(year, 1, 1))    # New Year's Day
        holidays |= cls._with_weekend_substitute(date(year, 1, 26))   # Australia Day
        holidays.add(date(year, 4, 25))                                 # Anzac Day (no substitute)
        easter = cls._easter_sunday(year)
        holidays.add(easter - timedelta(days=2))                       # Good Friday
        holidays.add(easter - timedelta(days=1))                       # Easter Saturday (NSW-specific)
        holidays.add(easter + timedelta(days=1))                       # Easter Monday
        holidays.add(cls._nth_weekday(year, 6, 0, 2))                  # King's/Queen's Birthday: 2nd Mon of June
        holidays.add(cls._nth_weekday(year, 10, 0, 1))                 # Labour Day: 1st Mon of October
        holidays |= cls._with_weekend_substitute(date(year, 12, 25))  # Christmas Day
        holidays |= cls._with_weekend_substitute(date(year, 12, 26))  # Boxing Day
        return holidays

    @classmethod
    def is_nsw_public_holiday(cls, d):
        y = d.year
        if y not in cls._holiday_cache:
            cls._holiday_cache[y] = cls.nsw_public_holidays(y)
        return d in cls._holiday_cache[y]

    # ---- Ausgrid EA025 Residential Demand window ---------------------
    # Two seasonal windows, weekdays only, NSW public holidays excluded.
    # Every other interval (weekends, holidays, shoulder months
    # Apr/May/Sep/Oct, off-window hours) carries zero demand risk.
    # Confirmed against real invoiced kW figures.

    def in_demand_window(self, dt):
        """True if the half-hour interval starting at datetime `dt`
        (local, naive - already in Sydney wall-clock time) counts toward
        the monthly demand-peak calculation."""
        if dt.weekday() >= 5:
            return False
        if self.is_nsw_public_holiday(dt.date()):
            return False
        month = dt.month
        hour = dt.hour
        if month in (6, 7, 8):          # winter: 5pm-9pm
            return 17 <= hour < 21
        if month in (11, 12, 1, 2, 3):  # summer: 2pm-8pm
            return 14 <= hour < 20
        return False                     # shoulder months (Apr/May/Sep/Oct): no demand charge

    # ---- tariff rates --------------------------------------------------

    def load_rate_periods(self):
        rates_path = os.path.join(self.resolve_data_home(), self.tariff_rates_filename())
        periods, _plan = load_rate_periods_from_file(
            rates_path, self.name, self.tariff_rates_example_path()
        )
        return periods

    def rate_for_date(self, d, periods=None):
        """The tariff period in effect on date `d` (a date object) - the
        last period whose effective_from is <= d."""
        periods = periods or self.load_rate_periods()
        applicable = [p for p in periods if p["_effective_from_date"] <= d]
        if not applicable:
            raise ValueError(f"No tariff period covers {d} - earliest known period starts "
                              f"{periods[0]['effective_from']}")
        return applicable[-1]

    # ---- solar credit / demand charge math -----------------------------

    def solar_credit(self, total_kwh, days, tier1_rate):
        cap = TIER1_CAP_KWH_PER_DAY * days
        tier1_kwh = min(total_kwh, cap)
        tier2_kwh = max(0.0, total_kwh - cap)
        return round(tier1_kwh * tier1_rate, 2), round(tier2_kwh * TIER2_RATE, 2)

    def demand_charge(self, kw_latched, billing_days, rate_per_kw_day):
        ex_gst = kw_latched * rate_per_kw_day * billing_days
        incl_gst = ex_gst * 1.10
        return round(ex_gst, 2), round(incl_gst, 2)
