#!/usr/bin/env python3
"""
ev_session_cost.py - Per-session EV-charging cost under the real AGL
Solar VIP tariff (AGENTS.md TODO-013)
=====================================================================
The actual answer REQ-027/031's whole body of AGL work was for: given one
real home-charging session, what did it actually cost - not Tessie's own
"Cost"/"Cost Per kWh" columns (those are the CAR's own guess, not AGL's
real dated rates), and not a share of the monthly demand charge (per the
user's explicit steer at the end of REQ-031, that stays a separate,
whole-account number in agl_demand_calculator.py/agl_cost_calculator.py,
never attributed to one session here).

Two cost figures are printed for every session:

  1. "base cost" - kWh x that date's dated usage rate (agl_tariff_rates.json),
     i.e. what it would have cost if 100% grid-imported. Always computed.

  2. "Evnex-adjusted" - splits the session's own kWh by Evnex's own
     `distributionByTariff` (its configured solar-hours-vs-flat-hours
     time-of-use split for that charge point, NOT a real household solar
     sensor reading - see the caveat printed with every result), then
     values the "solar" share as an OPPORTUNITY COST at that period's
     tier-1 feed-in rate rather than $0 - self-consuming that energy for
     charging meant not exporting it for a credit, so the true cost isn't
     free, it's what the credit would have been. (Uses the tier-1 rate as
     a flat approximation - see module docstring caveat below on why the
     real tiered/pooled cap isn't attributed per session.)

For every session this ALSO cross-checks Evnex's tariff-period claim
against AGL's own real smart-meter half-hourly readings
(agl_usage_master.csv) for the exact overlapping window(s) - real import
kWh recorded during a session Evnex called mostly-solar is flagged as a
conflict worth a second look, rather than silently trusted.

Deliberately NOT attempted here (left for a future PVOutput-based pass,
per Tessie/PVOUTPUT_API.md's "Fit with existing AGL/solar work" section):
disaggregating a session's own share of a half-hour's total household
import/export from everything else drawing power in the house at the
same time. AGL's smart meter reports one net figure per half-hour for
the WHOLE HOUSE, not per-device, so "real AGL meter" figures below are
the household total for that window, not proof of what the EV alone
drew - they're a plausibility check on Evnex's own claim, not a
replacement measurement.

Tier caveat: AGL's real solar credit is a POOLADED per-billing-period cap
(10 kWh/day x days-in-period at the tier-1 rate, everything beyond that
at a flat, lower tier-2 rate - see agl_cost_calculator.py's own
docstring). Attributing a fair tier-1-vs-tier-2 split to one session
would require knowing the whole period's running solar-export total up
to that moment, which this tool doesn't track. Every "Evnex-adjusted"
figure below assumes tier-1 pricing for its solar share as a simplifying
approximation - usually close, since most days don't push the period's
pooled export past the daily-averaged cap, but not exact once they do.

Data sources (both already built by earlier work - nothing new fetched
here):
  - Tessie/charges/deepdive/*_Evnex_Home*.json (REQ-030's auto-synced
    completed Evnex sessions - kWh, real UTC start/stop, tariff split)
  - AGL/usage/agl_usage_master.csv (REQ-031's consolidated half-hourly
    smart-meter import/export data)
  - AGL/agl_tariff_rates.json (REQ-031's dated tariff periods - PII,
    never committed, see agl_demand_calculator.py's own note)

Usage:
    ./ev_session_cost.py                       # 20 most recent sessions
    ./ev_session_cost.py --limit 5
    ./ev_session_cost.py --since-days 30
    ./ev_session_cost.py --all
    ./ev_session_cost.py --session-id 525f9c52-429b-496a-901d-eb1c002afee1
"""

import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agl_common import resolve_agl_data_home  # noqa: E402
from agl_demand_calculator import load_rate_periods, rate_for_date  # noqa: E402
from tessie_api_common import resolve_tessie_data_home  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

DEEPDIVE_DIR = os.path.join(resolve_tessie_data_home(), "charges", "deepdive")
USAGE_MASTER = os.path.join(resolve_agl_data_home(), "usage", "agl_usage_master.csv")

# A "material" real import figure during a window Evnex claims is mostly
# solar - scaled to the SESSION's own kWh, not a fixed absolute number.
# (An early version used a fixed 0.05 kWh floor and fired on nearly every
# multi-hour session - a household that's a strong net EXPORTER overall
# can still show a tiny import blip in one bucket, e.g. a cloud passing
# over, and that's not a real contradiction of a "mostly solar" claim.
# Scaling to the session's own size means a short session still gets a
# small absolute bar while a long one needs proportionally more real
# import before it's flagged.)
CONFLICT_IMPORT_RATIO = 0.25  # real import >= 25% of the session's own kWh
CONFLICT_SOLAR_PCT_THRESHOLD = 50.0


def iso_to_epoch(s):
    if not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def load_evnex_deepdive_sessions(deepdive_dir=None, since_days=None, limit=None, session_id=None):
    """Every Tessie/charges/deepdive/*_Evnex_Home*.json file's parsed
    content, most-recent-first by its own real charging_started_utc -
    never the filename (see AGENTS.md REQ-030 on why filenames here are
    for humans only)."""
    deepdive_dir = deepdive_dir or DEEPDIVE_DIR
    sessions = []
    for path in glob.glob(os.path.join(deepdive_dir, "*_Evnex_Home*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("source") != "evnex":
            continue
        d["_file"] = path
        sessions.append(d)

    def start_key(d):
        return iso_to_epoch(d.get("charging_started_utc")) or 0

    sessions.sort(key=start_key, reverse=True)

    if session_id:
        sessions = [s for s in sessions if s.get("evnex_session_id") == session_id]
    if since_days:
        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
        sessions = [s for s in sessions if start_key(s) >= cutoff]
    if limit:
        sessions = sessions[:limit]
    return sessions


def parse_agl_dt(s):
    return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M:%S %p")


_agl_index_cache = None


def load_agl_index(master_path):
    """{half_hour_start_naive_datetime: {"import": kWh, "solar_export": kWh}}
    - one pass through agl_usage_master.csv, cached for the life of this
    process so every session's lookup afterward is O(session length /
    30 min) instead of re-scanning the whole (~200k row) file each time."""
    global _agl_index_cache
    if _agl_index_cache is not None:
        return _agl_index_cache
    index = {}
    if not os.path.isfile(master_path):
        _agl_index_cache = index
        return index
    with open(master_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            role = row["Role"]
            if role not in ("import", "solar_export"):
                continue
            dt = parse_agl_dt(row["StartDate"])
            bucket = index.setdefault(dt, {})
            bucket[role] = bucket.get(role, 0.0) + float(row["kWh"])
    _agl_index_cache = index
    return index


def half_hour_floor(dt):
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def real_agl_overlap(master_path, local_start, local_end):
    """(import_kwh, export_kwh, buckets_found, buckets_missing) - the
    REAL whole-household AGL smart-meter total for every half-hour
    bucket overlapping [local_start, local_end) (naive Sydney-local
    datetimes). Whole-bucket sums, not prorated to the exact overlap
    fraction - fine for the short (well under an hour) sessions this
    dataset actually has; would need prorating for anything longer.
    `buckets_missing` counts buckets with no AGL data at all (outside
    an exported date range, or in the known 2022-05-31 to 2024-09-07
    gap - see AGENTS.md REQ-031) - never silently treated as zero."""
    index = load_agl_index(master_path)
    cur = half_hour_floor(local_start)
    import_kwh = export_kwh = 0.0
    found = missing = 0
    while cur < local_end:
        bucket = index.get(cur)
        if bucket is None:
            missing += 1
        else:
            found += 1
            import_kwh += bucket.get("import", 0.0)
            export_kwh += bucket.get("solar_export", 0.0)
        cur += timedelta(minutes=30)
    return import_kwh, export_kwh, found, missing


def solar_split(distribution):
    """(solar_pct, other_pct) out of 100 from Evnex's own
    `distributionByTariff` dict - any key containing "solar"
    (case-insensitive) counts toward the solar share, everything else
    counts as "other" (grid-rate). Returns (None, None) if there's no
    usable split (missing/empty - some real sessions have this, see
    module note)."""
    if not distribution:
        return None, None
    solar_pct = sum(v for k, v in distribution.items() if "solar" in k.lower())
    other_pct = sum(v for k, v in distribution.items() if "solar" not in k.lower())
    total = solar_pct + other_pct
    if total <= 0:
        return None, None
    return solar_pct, other_pct


def evaluate_session(session, periods, master_path=USAGE_MASTER):
    attrs = session.get("session", {}).get("attributes", {}) or {}
    te = attrs.get("totalEnergyUsage") or {}
    kwh_total = (te.get("total") or 0.0) / 1000.0  # Wh -> kWh

    start_epoch = iso_to_epoch(session.get("charging_started_utc"))
    end_epoch = iso_to_epoch(session.get("charging_stopped_utc")) or start_epoch
    zone = session.get("timezone") or "Australia/Sydney"
    try:
        tz = ZoneInfo(zone)
    except Exception:
        tz = ZoneInfo("Australia/Sydney")
    local_start = datetime.fromtimestamp(start_epoch, tz=timezone.utc).astimezone(tz).replace(tzinfo=None)
    local_end = datetime.fromtimestamp(end_epoch, tz=timezone.utc).astimezone(tz).replace(tzinfo=None)
    if local_end <= local_start:
        local_end = local_start + timedelta(minutes=1)  # degenerate/instant session - still needs a non-empty window to look up

    period = rate_for_date(local_start.date(), periods)
    usage_rate = period["usage_rate_per_kwh"]
    solar_tier1_rate = period["solar_feed_in_tier1_rate_per_kwh"]

    base_cost = kwh_total * usage_rate

    solar_pct, other_pct = solar_split(te.get("distributionByTariff"))
    if solar_pct is None:
        evnex_adjusted_cost = None
        solar_kwh = other_kwh = None
    else:
        solar_kwh = kwh_total * solar_pct / 100.0
        other_kwh = kwh_total * other_pct / 100.0
        evnex_adjusted_cost = other_kwh * usage_rate + solar_kwh * solar_tier1_rate

    import_kwh, export_kwh, buckets_found, buckets_missing = real_agl_overlap(master_path, local_start, local_end)

    conflict = (
        solar_pct is not None
        and solar_pct >= CONFLICT_SOLAR_PCT_THRESHOLD
        and kwh_total > 0
        and import_kwh >= CONFLICT_IMPORT_RATIO * kwh_total
    )

    return {
        "evnex_session_id": session.get("evnex_session_id"),
        "local_start": local_start,
        "local_end": local_end,
        "duration_min": round((local_end - local_start).total_seconds() / 60.0, 1),
        "kwh_total": kwh_total,
        "period_effective_from": period["effective_from"],
        "usage_rate": usage_rate,
        "solar_tier1_rate": solar_tier1_rate,
        "base_cost": base_cost,
        "solar_pct": solar_pct,
        "evnex_adjusted_cost": evnex_adjusted_cost,
        "real_import_kwh": import_kwh,
        "real_export_kwh": export_kwh,
        "buckets_found": buckets_found,
        "buckets_missing": buckets_missing,
        "conflict": conflict,
    }


def print_session(r):
    ts = r["local_start"].strftime("%Y-%m-%d %H:%M")
    print(f"{ts}  dur {r['duration_min']:>5.1f}min  kWh {r['kwh_total']:.3f}  "
          f"(rate period from {r['period_effective_from']}, ${r['usage_rate']}/kWh)")
    print(f"   base cost (100% grid)                 : ${r['base_cost']:.4f}")
    if r["evnex_adjusted_cost"] is None:
        print(f"   Evnex-adjusted                         : n/a (no distributionByTariff on this session)")
    else:
        print(f"   Evnex-adjusted (solar {r['solar_pct']:.1f}% @ ${r['solar_tier1_rate']}/kWh forgone credit) : ${r['evnex_adjusted_cost']:.4f}")
    if r["buckets_missing"]:
        print(f"   real AGL meter: {r['buckets_found']} bucket(s) found, {r['buckets_missing']} bucket(s) with NO usage data for this window (outside an exported range)")
    else:
        print(f"   real AGL meter (whole household, {r['buckets_found']} half-hour bucket(s)): import {r['real_import_kwh']:.3f} kWh, export {r['real_export_kwh']:.3f} kWh")
    if r["conflict"]:
        ratio_pct = 100.0 * r["real_import_kwh"] / r["kwh_total"] if r["kwh_total"] else 0.0
        print(f"   ⚠️  CONFLICT: Evnex claims {r['solar_pct']:.1f}% solar but AGL's real meter shows "
              f"{r['real_import_kwh']:.3f} kWh of actual grid import in this window - {ratio_pct:.0f}% of this "
              f"session's own kWh - worth a second look (remember: that's whole-household import, not proof "
              f"the EV itself drew it, but it's too large relative to the session to be background noise).")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=20, help="Most recent N sessions to evaluate (default: 20, ignored with --all)")
    parser.add_argument("--since-days", type=int, help="Only sessions from the last N days")
    parser.add_argument("--session-id", help="Evaluate one specific Evnex session by its UUID")
    parser.add_argument("--all", action="store_true", help="Evaluate every session found (overrides --limit)")
    parser.add_argument("--deepdive-dir", help=f"Override the deep-dive JSON folder (default: {DEEPDIVE_DIR})")
    parser.add_argument("--usage-master", default=USAGE_MASTER, help=f"Override the AGL usage master CSV path (default: {USAGE_MASTER})")
    args = parser.parse_args()

    limit = None if (args.all or args.session_id) else args.limit
    sessions = load_evnex_deepdive_sessions(
        deepdive_dir=args.deepdive_dir, since_days=args.since_days, limit=limit, session_id=args.session_id
    )
    if not sessions:
        print("No matching Evnex deep-dive sessions found.")
        return 1

    try:
        periods = load_rate_periods()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1

    results = [evaluate_session(s, periods, master_path=args.usage_master) for s in sessions]

    print(f"=== {len(results)} home-charging session(s) ===\n")
    for r in results:
        print_session(r)

    total_kwh = sum(r["kwh_total"] for r in results)
    total_base = sum(r["base_cost"] for r in results)
    n_adjusted = sum(1 for r in results if r["evnex_adjusted_cost"] is not None)
    # Blended total: Evnex-adjusted where a tariff split exists, plain base
    # cost for the sessions that have none - so this always covers every
    # session, never silently drops the ones with no split.
    total_blended = sum(
        r["evnex_adjusted_cost"] if r["evnex_adjusted_cost"] is not None else r["base_cost"]
        for r in results
    )
    n_conflict = sum(1 for r in results if r["conflict"])

    print("=== Totals ===")
    print(f"  {len(results)} session(s), {total_kwh:.3f} kWh total")
    print(f"  base cost (100% grid, every session)                         : ${total_base:.2f}")
    print(f"  blended best estimate (Evnex split where available, {n_adjusted}/{len(results)} sessions,")
    print(f"                         base cost for the rest)               : ${total_blended:.2f}")
    if n_conflict:
        print(f"  ⚠️  {n_conflict} session(s) flagged with a solar-claim/real-import conflict - see above")
    return 0


if __name__ == "__main__":
    main()
