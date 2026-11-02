"""power_demand.py - the power provider's rate periods and its demand charge (kW latched in a month x days x rate), for the power commands, the charge costs and the app."""

import calendar
from datetime import date
from providers.power import get_power_provider
from lib.power_usage_db import get_usage_db_connection, iter_rows


def load_rate_periods():
    """Sorted dated tariff periods for the active power provider."""
    return get_power_provider().load_rate_periods()


def rate_for_date(d, periods=None):
    """The active provider's tariff period in effect on date `d` (a
    date object) - the last period whose effective_from is <= d."""
    return get_power_provider().rate_for_date(d, periods)


def load_import_readings(data_home, year, month):
    """[(datetime, kWh), ...] for every "import"-role row in the usage
    database (lib/power_usage_db.py) whose StartDate falls in the given
    calendar year/month - datetime parsing follows the active provider's
    own usage-export timestamp format. REQ-115: queried via the derived
    start_date_iso column (indexed, correctly bounded), replacing the old
    full-file agl_usage_master.csv linear scan."""
    provider = get_power_provider()
    conn = get_usage_db_connection(provider, data_home)
    try:
        last_day = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, last_day)
        out = []
        for row in iter_rows(conn, role="import", start_date=month_start, end_date=month_end):
            dt = provider.parse_datetime(row["StartDate"])
            if dt.year == year and dt.month == month:  # belt-and-suspenders vs. any TZ/boundary edge case
                out.append((dt, float(row["kWh"])))
        return out
    finally:
        conn.close()


def monthly_latched_kw(data_home, year, month):
    """(kw, at_datetime) for the latched demand peak in this calendar
    month under the active provider's own demand window, or (0.0, None)
    if no interval in the demand window this month (e.g. a shoulder
    month)."""
    provider = get_power_provider()
    readings = load_import_readings(data_home, year, month)
    candidates = [(dt, kwh) for dt, kwh in readings if provider.in_demand_window(dt)]
    if not candidates:
        return 0.0, None
    dt, kwh = max(candidates, key=lambda x: x[1])
    return round(kwh * 2, 2), dt


def demand_charge(kw_latched, billing_days, rate_per_kw_day):
    return get_power_provider().demand_charge(kw_latched, billing_days, rate_per_kw_day)
