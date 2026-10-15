#!/usr/bin/env python3
"""
pv_solaredge.py - REQ-127: SolarEdgeProvider, the first (and currently
only) real providers_pv/ implementation. Thin adapter over
lib/solaredge_common.py's already-built, already-live-tested API
client (REQ-125) - this file's job is only to satisfy BasePvProvider's
interface, not to re-implement the HTTP/parsing logic.
"""

import os
from datetime import datetime, timedelta

from lib.solaredge_common import (
    MAX_ENERGY_DETAILS_DAYS_PER_CALL,
    api_get,
    fetch_energy_details_range,
    get_api_key,
    get_site_id,
)
from providers_pv.pv_base import BasePvProvider

# REQ-127: confirmed live - a QUARTER_OF_AN_HOUR request over ~2 months
# gets a 400 "Data range interval exceeds one month" (the DAY-level cap,
# MAX_ENERGY_DETAILS_DAYS_PER_CALL=365, is a full order of magnitude
# looser - confirmed separately in REQ-125). Kept at 28 (not "~30") to
# stay clear of any calendar-month-length edge case in SolarEdge's own
# "one month" check, the same reasoning REQ-125 already applied to the
# 365-vs-366 day-cap edge.
MAX_INTRADAY_DAYS_PER_CALL = 28


def _fetch_energy_details_range_at(date_from, date_to, api_key, site_id, time_unit, max_days_per_call, timeout=30):
    """Generalized version of lib/solaredge_common.py's
    fetch_energy_details_range() (which is hardcoded to timeUnit=DAY) -
    same chunking/parsing shape, parameterized on time_unit and the
    caller's own per-call day cap so this one function serves both the
    daily and intraday paths without duplicating the HTTP/parsing
    logic twice."""
    from lib.solaredge_common import METER_TO_FIELD

    by_ts = {}
    errors = []
    chunk_start = date_from
    while chunk_start <= date_to:
        chunk_end = min(date_to, chunk_start + timedelta(days=max_days_per_call - 1))
        params = {
            "timeUnit": time_unit,
            "startTime": f"{chunk_start.isoformat()} 00:00:00",
            "endTime": f"{chunk_end.isoformat()} 23:59:59",
        }
        status, parsed, body = api_get(f"site/{site_id}/energyDetails", api_key, params=params, timeout=timeout)
        if status != 200 or not parsed or "energyDetails" not in parsed:
            detail = (parsed or {}).get("detail") if isinstance(parsed, dict) else None
            errors.append(f"{chunk_start}..{chunk_end}: HTTP {status}: {detail or body[:200]}")
        else:
            for meter in parsed["energyDetails"].get("meters", []):
                field = METER_TO_FIELD.get(meter.get("type"))
                if not field:
                    continue
                for v in meter.get("values", []):
                    if v.get("value") is None:
                        continue
                    ts = datetime.strptime(v["date"], "%Y-%m-%d %H:%M:%S")
                    by_ts.setdefault(ts, {})[field] = v["value"] / 1000.0
        chunk_start = chunk_end + timedelta(days=1)

    rows = []
    for ts in sorted(by_ts):
        rec = by_ts[ts]
        rows.append({
            "ts": ts,
            "production_kwh": rec.get("production_kwh"),
            "consumption_kwh": rec.get("consumption_kwh"),
            "feedin_kwh": rec.get("feedin_kwh"),
            "purchased_kwh": rec.get("purchased_kwh"),
            "self_consumption_kwh": rec.get("self_consumption_kwh"),
        })
    return rows, ("; ".join(errors) if errors else None)


class SolarEdgeProvider(BasePvProvider):
    name = "solaredge"
    display_name = "SolarEdge"

    max_daily_days_per_call = MAX_ENERGY_DETAILS_DAYS_PER_CALL  # 365 (REQ-125)
    max_intraday_days_per_call = MAX_INTRADAY_DAYS_PER_CALL  # 28 (REQ-127)
    intraday_interval_minutes = 15

    def resolve_credentials(self, config):
        return get_api_key(None, config), get_site_id(None, config)

    def install_date(self, api_key, site_id):
        status, parsed, _body = api_get(f"site/{site_id}/details", api_key)
        if status == 200 and parsed:
            d = parsed.get("details", {}).get("installationDate")
            if d:
                return datetime.strptime(d, "%Y-%m-%d").date()
        return None

    def fetch_daily_range(self, date_from, date_to, api_key, site_id):
        rows, err = fetch_energy_details_range(date_from, date_to, api_key, site_id)
        return [{"day": r["day"], **{k: r[k] for k in
                 ("production_kwh", "consumption_kwh", "feedin_kwh", "purchased_kwh", "self_consumption_kwh")}}
                for r in rows], err

    def fetch_intraday_range(self, date_from, date_to, api_key, site_id):
        rows, err = _fetch_energy_details_range_at(
            date_from, date_to, api_key, site_id,
            time_unit="QUARTER_OF_AN_HOUR", max_days_per_call=MAX_INTRADAY_DAYS_PER_CALL,
        )
        return [{"timestamp": r["ts"], **{k: r[k] for k in
                 ("production_kwh", "consumption_kwh", "feedin_kwh", "purchased_kwh", "self_consumption_kwh")}}
                for r in rows], err
