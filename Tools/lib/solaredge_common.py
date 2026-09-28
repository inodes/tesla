#!/usr/bin/env python3
"""
solaredge_common.py - REQ-125: direct SolarEdge Monitoring API client.

Why this exists: PVOutput's getoutput.jsp only ever gives Generation/
Exported/Used, and this account's own real limit turned out to be 50
records per call regardless of donation tier (see REQ-124) - a full
~7-year backfill needs ~45 paced calls. SolarEdge's own API, direct
from the inverter's own monitoring account (api-docs.solaredge.com),
gives a MUCH better shape of the same real data for free:

  - energyDetails (timeUnit=DAY) returns FIVE meters per day directly -
    Production, Consumption, FeedIn (export), Purchased (grid import),
    and SelfConsumption - so self-consumption doesn't need to be
    DERIVED (generation - exported) the way it does from PVOutput; it's
    a real meter SolarEdge itself already computed.
  - Confirmed live: dataPeriod for this real site (1393211) spans the
    exact install date (2019-11-28) through today - the full history
    genuinely exists, matching what the user could already see in the
    SolarEdge UI.
  - Confirmed live: a single call can span up to ONE YEAR
    (startDate/startTime..endDate/endTime) - a 400 error explicitly
    says "Data range interval exceeds one year" past that. So the
    entire ~7-year history needs only ~7-8 calls, not ~45.

Auth is a query-string api_key (not a header, unlike PVOutput) - see
fetch_energy_details_range() below.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from lib.tessie_api_common import load_config

API_BASE = "https://monitoringapi.solaredge.com"

# Confirmed live (REQ-125): a 366+ day span gets a 400 "Data range
# interval exceeds one year" - kept at 365 (not 366) to stay clear of
# any leap-year edge case in SolarEdge's own "one year" check.
MAX_ENERGY_DETAILS_DAYS_PER_CALL = 365

METER_TO_FIELD = {
    "Production": "production_kwh",
    "Consumption": "consumption_kwh",
    "FeedIn": "feedin_kwh",
    "Purchased": "purchased_kwh",
    "SelfConsumption": "self_consumption_kwh",
}


def get_api_key(cli_value, config):
    return cli_value or os.environ.get("SOLAREDGE_API_KEY") or config.get("solaredge", {}).get("api_key")


def get_site_id(cli_value, config):
    return cli_value or os.environ.get("SOLAREDGE_SITE_ID") or config.get("solaredge", {}).get("site_id")


def api_get(endpoint, api_key, params=None, timeout=30):
    """GET {API_BASE}/{endpoint}?...&api_key=... . Returns (status_or_None,
    parsed_json_or_None, raw_body_bytes). SolarEdge returns a JSON body
    (RFC7807 problem+json) even on 4xx errors, unlike PVOutput's
    plain-text error bodies - always parsed when possible so a caller
    can read e.g. `detail` without re-decoding."""
    q = dict(params or {})
    q["api_key"] = api_key
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "tesla-tools/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read()
        status = e.code
    except Exception as e:
        return None, None, str(e).encode()
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None
    return status, parsed, body


def fetch_energy_details_range(date_from, date_to, api_key, site_id, timeout=30):
    """[{"day": date_obj, "production_kwh", "consumption_kwh",
    "feedin_kwh", "purchased_kwh", "self_consumption_kwh"}, ...] for
    every day SolarEdge reports in [date_from, date_to] (inclusive
    date() objects) - chunked at MAX_ENERGY_DETAILS_DAYS_PER_CALL (the
    API's own per-call day cap - not a choice made here). Returns
    (rows, error_or_None) - a day missing from ALL five meters is
    simply absent from rows (SolarEdge sends null for a day with no
    reading), never a zero."""
    by_day = {}
    errors = []
    chunk_start = date_from
    while chunk_start <= date_to:
        chunk_end = min(date_to, chunk_start + timedelta(days=MAX_ENERGY_DETAILS_DAYS_PER_CALL - 1))
        params = {
            "timeUnit": "DAY",
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
                    d = datetime.strptime(v["date"], "%Y-%m-%d %H:%M:%S").date()
                    by_day.setdefault(d, {})[field] = v["value"] / 1000.0
        chunk_start = chunk_end + timedelta(days=1)

    rows = []
    for d in sorted(by_day):
        rec = by_day[d]
        rows.append({
            "day": d,
            "production_kwh": rec.get("production_kwh"),
            "consumption_kwh": rec.get("consumption_kwh"),
            "feedin_kwh": rec.get("feedin_kwh"),
            "purchased_kwh": rec.get("purchased_kwh"),
            "self_consumption_kwh": rec.get("self_consumption_kwh"),
        })
    return rows, ("; ".join(errors) if errors else None)
