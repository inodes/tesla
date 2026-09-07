#!/usr/bin/env python3
"""
agl_common.py - Shared AGL/Ausgrid plumbing: where the consolidated AGL
data lives, and the NSW public-holiday + seasonal demand-window rules
needed to reproduce Ausgrid's EA025 Residential Demand tariff correctly.

Design note (see AGENTS.md REQ-031): AGL's own invoice PRINTS the demand
window as "2pm-8pm on working weekdays" year-round, but the actual
CHARGED figure only reproduces correctly under a season-aware window -
confirmed by reconciling real half-hourly usage data against real
invoiced kW figures for winter months (see agl_demand_calculator.py's
self-test). The printed label on the bill is not trustworthy; the
window logic here is derived from what the bill actually CHARGES, not
from what it prints.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tessie_api_common import load_config  # noqa: E402


def resolve_agl_data_home():
    """The one authoritative home for AGL-derived data (usage master,
    tariff rates, demand-charge output) - config.json's own
    "agl_directory" if set, falling back to a sibling of the existing
    Tessie/ data folder. Distinct from where the RAW invoices/usage
    exports live (config.json's "agl_invoices_directory" /
    ~/iCloud/PDF/AGL - the general household document archive, not
    Tesla-repo-managed data) - this is where derived/consolidated
    output goes, mirroring resolve_tessie_data_home()'s role for Tessie."""
    cfg = load_config()
    cfg_dir = cfg.get("agl_directory")
    if cfg_dir:
        return os.path.abspath(os.path.expanduser(cfg_dir))
    return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/AGL")


def resolve_agl_invoices_dir():
    """Where the raw AGL invoice PDFs and usage-export CSVs actually
    live - config.json's "agl_invoices_directory" if set, falling back
    to the observed real location (~/iCloud/PDF/AGL). Kept separate
    from resolve_agl_data_home() deliberately - these are the RAW
    source documents (the household's general PDF archive), not
    Tesla-repo-managed derived data."""
    cfg = load_config()
    cfg_dir = cfg.get("agl_invoices_directory")
    if cfg_dir:
        return os.path.abspath(os.path.expanduser(cfg_dir))
    return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/AGL")


# -----------------------------------------------------------------------------
# NSW public holidays
# -----------------------------------------------------------------------------
# Computed, not hardcoded per-year - covers any year in the usage data
# without needing an annual update. Weekend-substitution (a fixed-date
# holiday moves to the following Monday when it falls on a Sat/Sun) is
# applied only to the holidays where it's well-established NSW practice:
# New Year's Day, Australia Day, Christmas Day, Boxing Day. Anzac Day and
# Labour Day are included for completeness but never affect the demand
# calculation below (neither falls inside the Jun-Aug or Nov-Mar windows).
# This is a best-effort reconstruction of the NSW statutory calendar, not
# sourced from an official feed - if a specific disputed date matters
# (e.g. the "EV commissioning" arrears-trap scenario), double check that
# one date against the NSW government's own public holiday list.

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


def _nth_weekday(year, month, weekday, n):
    """The date of the n-th occurrence (1-indexed) of `weekday` (0=Mon)
    in `year`/`month`."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d = d + timedelta(days=offset + 7 * (n - 1))
    return d


def _with_weekend_substitute(d):
    """A fixed-date holiday that falls on Sat/Sun also produces a
    substitute public holiday on the following Monday. Returns the set
    of actual public-holiday dates this one date produces (1 or 2)."""
    if d.weekday() == 5:  # Saturday
        return {d, d + timedelta(days=2)}
    if d.weekday() == 6:  # Sunday
        return {d, d + timedelta(days=1)}
    return {d}


def nsw_public_holidays(year):
    """Set of date() objects - NSW statutory public holidays for `year`."""
    holidays = set()
    holidays |= _with_weekend_substitute(date(year, 1, 1))    # New Year's Day
    holidays |= _with_weekend_substitute(date(year, 1, 26))   # Australia Day
    holidays.add(date(year, 4, 25))                             # Anzac Day (no substitute)
    easter = _easter_sunday(year)
    holidays.add(easter - timedelta(days=2))                   # Good Friday
    holidays.add(easter - timedelta(days=1))                   # Easter Saturday (NSW-specific)
    holidays.add(easter + timedelta(days=1))                   # Easter Monday
    holidays.add(_nth_weekday(year, 6, 0, 2))                  # King's/Queen's Birthday: 2nd Mon of June
    holidays.add(_nth_weekday(year, 10, 0, 1))                 # Labour Day: 1st Mon of October
    holidays |= _with_weekend_substitute(date(year, 12, 25))  # Christmas Day
    holidays |= _with_weekend_substitute(date(year, 12, 26))  # Boxing Day
    return holidays


_holiday_cache = {}


def is_nsw_public_holiday(d):
    y = d.year
    if y not in _holiday_cache:
        _holiday_cache[y] = nsw_public_holidays(y)
    return d in _holiday_cache[y]


# -----------------------------------------------------------------------------
# Ausgrid EA025 Residential Demand window
# -----------------------------------------------------------------------------
# Two seasonal windows, weekdays only, NSW public holidays excluded. Every
# other interval (weekends, holidays, shoulder months Apr/May/Sep/Oct,
# off-window hours) carries zero demand risk. Confirmed against real
# invoiced kW figures - see agl_demand_calculator.py's self-test.

def in_demand_window(dt):
    """True if the half-hour interval starting at datetime `dt` (local,
    naive - already in Sydney wall-clock time) counts toward the monthly
    demand-peak calculation."""
    if dt.weekday() >= 5:
        return False
    if is_nsw_public_holiday(dt.date()):
        return False
    month = dt.month
    hour = dt.hour
    if month in (6, 7, 8):          # winter: 5pm-9pm
        return 17 <= hour < 21
    if month in (11, 12, 1, 2, 3):  # summer: 2pm-8pm
        return 14 <= hour < 20
    return False                     # shoulder months (Apr/May/Sep/Oct): no demand charge
