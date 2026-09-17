#!/usr/bin/env python3
"""
analyse_session_cost.py - Per-session EV-charging cost under the real AGL
Solar VIP tariff (AGENTS.md TODO-013)
=====================================================================
The actual answer REQ-027/031's whole body of AGL work was for: given one
real home-charging session, what did it actually cost - not Tessie's own
"Cost"/"Cost Per kWh" columns (those are the CAR's own guess, not AGL's
real dated rates), and not a share of the monthly demand charge (per the
user's explicit steer at the end of REQ-031, that stays a separate,
whole-account number in power_demand_calculator.py/power_cost_calculator.py
(providers_power/power_agl.py's AGLProvider, REQ-070),
never attributed to one session here).

Three cost figures are printed for every session:

  1. "base cost" - kWh x that date's dated usage rate (agl_tariff_rates.json),
     i.e. what it would have cost if 100% grid-imported. Always computed.

  2. "PVOutput-adjusted" (REQ-071, 2026-09-15, PRIMARY) - splits the
     session's own kWh by a real household solar-self-consumption
     fraction measured directly from PVOutput's own TWO CT clamps
     (generation AND consumption, independently - lib/pvoutput_common.py's
     pv_window_split(), summed at 5-minute-interval resolution, not just
     from day-level totals) for the session's own window. This is the
     user's own explicit steer (AGENTS.local.md REQ-071): of the three
     data sources this repo integrates, Evnex only sees the car's own
     usage plus a single CT clamp (good enough for ITS OWN solar%
     estimate, but see #3 below for why that estimate isn't a live
     measurement); AGL only sees import/export and is manual-sync
     besides; PVOutput alone can MEASURE - not infer - real household
     self-consumption. Same opportunity-cost solar valuation as #3 below
     (tier-1 rate, GST-free). Falls back to #3, then #1, whenever
     PVOutput data isn't available or doesn't cover the window well
     (see evaluate_session()'s own pv_coverage handling).

  3. "Evnex-adjusted" (fallback) - splits the session's own kWh by
     Evnex's own `distributionByTariff`. IMPORTANT (user-confirmed,
     AGENTS.md REQ-040): this is NOT the charger's real CT-clamp solar
     reading - the Evnex charger genuinely DOES have CT clamps and a
     real solar-aware charging mode (only auto-starts once export passes
     a configured minimum, ~1.5kWh, of solar export), but
     `distributionByTariff` itself comes from a separate, simpler
     "tariff hours" schedule entered on Evnex's own website (which hours
     count as "solar" vs "flat") - a configured assumption, not that
     session's own real CT-clamp measurement (that's exactly why #2
     above, a real measurement, is now primary and this is the
     fallback). Then values the "solar" share as an OPPORTUNITY COST at
     that period's tier-1 feed-in rate rather than $0 - self-consuming
     that energy for charging meant not exporting it for a credit, so
     the true cost isn't free, it's what the credit would have been.
     (Uses the tier-1 rate as a flat approximation - see module
     docstring caveat below on why the real tiered/pooled cap isn't
     attributed per session.)

For every session this ALSO cross-checks Evnex's tariff-period claim
against AGL's own real smart-meter half-hourly readings
(agl_usage_master.csv) for the exact overlapping window(s) - real import
kWh recorded during a session Evnex called mostly-solar is flagged as a
conflict worth a second look, rather than silently trusted. A SEPARATE
cross-check (REQ-071, pv_agl_conflict) compares PVOutput's own real
import figure for the window against AGL's - two independent real
meters for the same physical quantity that should roughly agree; a
material mismatch there is a data-quality flag (clock drift, an
un-synced PVOutput day, a stale AGL export), not a claim about solar
usage. One confirmed,
plausible explanation for a flagged conflict (AGENTS.md REQ-040): the user
can manually override the charger's own real solar-export-threshold
auto-start feature and force a charge regardless of real solar export -
"that's where the expensive charges come from" - which would show up here
as real grid import during a window still labelled "solar" by the tariff-
hours schedule. A second, non-deliberate explanation (AGENTS.md REQ-050):
the Evnex app's own weekly schedule tags each time block as Charge/Don't
charge/Solar diversion, and a Solar-diversion block still pulls from the
grid whenever the HOUSE's own load eats into the export margin - no
override needed, the car just gets less sun than the schedule assumed.
Either way, a single Solar-diversion block's real current can cycle on
and off as export rises and falls, which is also why Tessie's own charge
log can show several short, separate charge records for what was really
one continuous plug-in session (see analysis_charge_curve.py / REQ-050 -
that's the multi-cycle case its historical-pooling and filename-stem
matching both have to account for). This tool doesn't (yet) distinguish
an override from either of these; it just surfaces the mismatch.

Deliberately NOT attempted here (left for a future PVOutput-based pass,
per Tessie/PVOUTPUT_API.md's "Fit with existing AGL/solar work" section):
disaggregating a session's own share of a half-hour's total household
import/export from everything else drawing power in the house at the
same time. AGL's smart meter reports one net figure per half-hour for
the WHOLE HOUSE, not per-device, so "real AGL meter" figures below are
the household total for that window, not proof of what the EV alone
drew - they're a plausibility check on Evnex's own claim, not a
replacement measurement.

Tier caveat: AGL's real solar credit is a POOLED per-billing-period cap
(10 kWh/day x days-in-period at the tier-1 rate, everything beyond that
at a flat, lower tier-2 rate - see power_cost_calculator.py's own
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
    never committed, see power_demand_calculator.py's own note)

Usage:
    ./analysis_ev_session_cost.py                       # 20 most recent sessions
    ./analysis_ev_session_cost.py --limit 5
    ./analysis_ev_session_cost.py --since-days 30
    ./analysis_ev_session_cost.py --all
    ./analysis_ev_session_cost.py --session-id 525f9c52-429b-496a-901d-eb1c002afee1
"""

import argparse
import csv
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402
from power_demand_calculator import load_rate_periods, rate_for_date  # noqa: E402
from lib.evnex_common import is_real_charging_session  # noqa: E402
from lib.tessie_api_common import resolve_tessie_data_home  # noqa: E402
from lib.pvoutput_common import (  # noqa: E402
    load_config as pv_load_config,
    get_api_key as pv_get_api_key,
    get_system_id as pv_get_system_id,
    pv_window_split,
    pv_agl_import_conflict,
)
from zoneinfo import ZoneInfo  # noqa: E402

DEEPDIVE_DIR = os.path.join(resolve_tessie_data_home(), "charges", "deepdive")
_POWER_PROVIDER = get_power_provider()
USAGE_MASTER = os.path.join(_POWER_PROVIDER.resolve_data_home(), "usage", _POWER_PROVIDER.usage_master_filename())

# REQ-071 (2026-09-15): PVOutput credentials, resolved once at import
# time (same "load once, reuse everywhere" pattern as USAGE_MASTER
# above), plus a module-level per-calendar-day fetch cache
# (_PV_DAY_CACHE) shared across every evaluate_session() call in one
# batch run - mirrors load_agl_index()'s own _agl_index_cache pattern
# below, and matters for the same reason: PVOutput's rate limit
# (300/hr donation-tier, 60/hr free - Tessie/PVOUTPUT_API.md) would
# otherwise get hit hard by a --all run over hundreds of sessions that
# often share the same handful of calendar days.
_PV_CFG = pv_load_config()
_PV_KEY = pv_get_api_key(None, _PV_CFG)
_PV_SID = pv_get_system_id(None, _PV_CFG)
_PV_DAY_CACHE = {}

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

    # GST treatment confirmed against real invoices elsewhere in this repo
    # (power_cost_calculator.py's reconstruct_bill(), agl_tariff_rates.
    # example.json's own "_comment") - agl_tariff_rates.json's rates are
    # ex GST, GST applies to usage/supply/demand CHARGES only, and the
    # solar feed-in credit is GST-free and simply netted off afterward
    # ("Total GST" on every real sampled invoice = 10% of charges, not
    # 10% of the post-credit net). The "Evnex-adjusted" figure here
    # values the solar-attributed share as that SAME kind of GST-free
    # credit (an opportunity cost, not a real charge), so it gets the
    # same treatment: GST on the grid-attributed share only, none on the
    # solar-attributed share. Caught missing entirely in an earlier
    # version (AGENTS.md TODO-027) when the user manually reproduced this
    # calculation by hand and it didn't match a GST-inclusive figure
    # shown right next to it in analyse_charging.py's own
    # inspect_session() box.
    GST_MULTIPLIER = 1.10

    base_cost = kwh_total * usage_rate
    base_cost_incl_gst = base_cost * GST_MULTIPLIER

    solar_pct, other_pct = solar_split(te.get("distributionByTariff"))
    if solar_pct is None:
        evnex_adjusted_cost = None
        evnex_adjusted_cost_incl_gst = None
        solar_kwh = other_kwh = None
    else:
        solar_kwh = kwh_total * solar_pct / 100.0
        other_kwh = kwh_total * other_pct / 100.0
        grid_charge_ex_gst = other_kwh * usage_rate
        solar_credit_component = solar_kwh * solar_tier1_rate  # GST-free
        evnex_adjusted_cost = grid_charge_ex_gst + solar_credit_component
        evnex_adjusted_cost_incl_gst = grid_charge_ex_gst * GST_MULTIPLIER + solar_credit_component

    import_kwh, export_kwh, buckets_found, buckets_missing = real_agl_overlap(master_path, local_start, local_end)

    conflict = (
        solar_pct is not None
        and solar_pct >= CONFLICT_SOLAR_PCT_THRESHOLD
        and kwh_total > 0
        and import_kwh >= CONFLICT_IMPORT_RATIO * kwh_total
    )

    # REQ-071: PVOutput-primary solar split - real generation+consumption
    # CT clamps for this session's own window (module-level _PV_DAY_CACHE
    # shares one fetch per calendar day across a whole batch run). Never
    # fatal to any of the figures above if it fails (missing credentials,
    # network egress, rate limit, an unexpected response shape) -
    # evnex_adjusted_cost above stays available as the fallback.
    pv_split = {
        "generation_kwh": None, "consumption_kwh": None, "self_consumed_kwh": None,
        "export_kwh": None, "import_kwh": None, "solar_fraction": None, "coverage": 0.0,
        "error": "no PVOutput credentials configured",
    }
    if _PV_KEY and _PV_SID:
        try:
            pv_split = pv_window_split(local_start, local_end, _PV_KEY, _PV_SID, day_cache=_PV_DAY_CACHE)
        except Exception as e:
            pv_split = dict(pv_split)
            pv_split["error"] = str(e)

    pv_adjusted_cost = None
    pv_adjusted_cost_incl_gst = None
    if pv_split["solar_fraction"] is not None and kwh_total > 0:
        pv_solar_kwh = kwh_total * pv_split["solar_fraction"]
        pv_grid_kwh = kwh_total * (1.0 - pv_split["solar_fraction"])
        pv_grid_charge_ex_gst = pv_grid_kwh * usage_rate
        pv_solar_credit_component = pv_solar_kwh * solar_tier1_rate  # GST-free, same treatment as evnex_adjusted_cost
        pv_adjusted_cost = pv_grid_charge_ex_gst + pv_solar_credit_component
        pv_adjusted_cost_incl_gst = pv_grid_charge_ex_gst * GST_MULTIPLIER + pv_solar_credit_component

    pv_agl_conflict = pv_agl_import_conflict(pv_split["import_kwh"], pv_split["coverage"], import_kwh, buckets_missing)

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
        "base_cost_incl_gst": base_cost_incl_gst,
        "solar_pct": solar_pct,
        "evnex_adjusted_cost": evnex_adjusted_cost,
        "evnex_adjusted_cost_incl_gst": evnex_adjusted_cost_incl_gst,
        "real_import_kwh": import_kwh,
        "real_export_kwh": export_kwh,
        "buckets_found": buckets_found,
        "buckets_missing": buckets_missing,
        "conflict": conflict,
        "pv_generation_kwh": pv_split["generation_kwh"],
        "pv_consumption_kwh": pv_split["consumption_kwh"],
        "pv_self_consumed_kwh": pv_split["self_consumed_kwh"],
        "pv_export_kwh": pv_split["export_kwh"],
        "pv_import_kwh": pv_split["import_kwh"],
        "pv_solar_fraction": pv_split["solar_fraction"],
        "pv_coverage": pv_split["coverage"],
        "pv_error": pv_split["error"],
        "pv_adjusted_cost": pv_adjusted_cost,
        "pv_adjusted_cost_incl_gst": pv_adjusted_cost_incl_gst,
        "pv_agl_conflict": pv_agl_conflict,
    }


def print_session(r):
    ts = r["local_start"].strftime("%Y-%m-%d %H:%M")
    print(f"{ts}  dur {r['duration_min']:>5.1f}min  kWh {r['kwh_total']:.3f}  "
          f"(rate period from {r['period_effective_from']}, ${r['usage_rate']}/kWh)")
    print(f"   base cost (100% grid)                 : ${r['base_cost']:.4f} ex GST -> ${r['base_cost_incl_gst']:.4f} incl GST")
    if r.get("pv_adjusted_cost_incl_gst") is not None:
        pv_pct = r["pv_solar_fraction"] * 100.0
        cov_note = "" if r.get("pv_coverage", 1.0) >= 0.99 else f", {r['pv_coverage']*100:.0f}% window coverage"
        print(f"   PVOutput-adjusted (PRIMARY, solar {pv_pct:.1f}% real measured{cov_note}) : "
              f"${r['pv_adjusted_cost']:.4f} ex GST -> ${r['pv_adjusted_cost_incl_gst']:.4f} incl GST")
    else:
        print(f"   PVOutput-adjusted (PRIMARY)            : unavailable ({r.get('pv_error') or 'unknown reason'}) - falling back to Evnex/base below")
    if r["evnex_adjusted_cost"] is None:
        print(f"   Evnex-adjusted (fallback)               : n/a (no distributionByTariff on this session)")
    else:
        print(f"   Evnex-adjusted (fallback, solar {r['solar_pct']:.1f}% @ ${r['solar_tier1_rate']}/kWh forgone credit) : "
              f"${r['evnex_adjusted_cost']:.4f} ex GST -> ${r['evnex_adjusted_cost_incl_gst']:.4f} incl GST "
              f"(GST-free on the solar-attributed share, same as a real feed-in credit)")
    if r["buckets_missing"]:
        print(f"   real AGL meter: {r['buckets_found']} bucket(s) found, {r['buckets_missing']} bucket(s) with NO usage data for this window (outside an exported range)")
    else:
        print(f"   real AGL meter (whole household, {r['buckets_found']} half-hour bucket(s)): import {r['real_import_kwh']:.3f} kWh, export {r['real_export_kwh']:.3f} kWh")
    if r.get("pv_generation_kwh") is not None:
        print(f"   PVOutput gen/cons (whole household, this window): {r['pv_generation_kwh']:.3f} kWh gen, {r['pv_consumption_kwh']:.3f} kWh cons -> "
              f"{r['pv_self_consumed_kwh']:.3f} kWh self-consumed, {r['pv_export_kwh']:.3f} kWh export, {r['pv_import_kwh']:.3f} kWh import")
    if r["conflict"]:
        ratio_pct = 100.0 * r["real_import_kwh"] / r["kwh_total"] if r["kwh_total"] else 0.0
        print(f"   ⚠️  CONFLICT: Evnex claims {r['solar_pct']:.1f}% solar but AGL's real meter shows "
              f"{r['real_import_kwh']:.3f} kWh of actual grid import in this window - {ratio_pct:.0f}% of this "
              f"session's own kWh - worth a second look (that's whole-household import, not proof the EV "
              f"itself drew it, but too large relative to the session to be background noise). Plausible "
              f"explanation: a manual override of the charger's own solar-export-threshold auto-start "
              f"(real CT-clamp based) forcing a charge regardless of actual solar that day.")
    if r.get("pv_agl_conflict"):
        print(f"   ⚠️  CONFLICT: PVOutput's real import for this window ({r['pv_import_kwh']:.3f} kWh) disagrees with "
              f"AGL's real meter import ({r['real_import_kwh']:.3f} kWh) by >=30% - a data-quality issue (clock drift, "
              f"an un-synced PVOutput day, a stale AGL export), not a solar-usage question. See REQ-071.")
    print()


def reconcile_against_invoices(deepdive_dir=None, master_path=USAGE_MASTER, periods=None, log=print):
    """Cross-references AGL's real invoiced totals
    (power_cost_calculator.REAL_INVOICE_CYCLES - already self-tested
    against reconstruct_bill() there) against this file's own computed
    per-session EV charging costs, for each known real billing cycle
    (REQ-071 - the user's own "AGL invoices can be used to cross
    reference the data as we've determined it").

    AGL's invoice is a WHOLE-HOUSEHOLD total, not itemised by device -
    there's no ground-truth "EV charging cost" line on a real bill to
    check evaluate_session()'s own per-session numbers against
    directly. What CAN honestly be checked, for each cycle:

      1. reconstruct_bill()'s own whole-household total still matches
         the real invoice - re-reported here for context, not a NEW
         check (power_cost_calculator.py's own self_test() already
         verifies this). This is the anchor: as long as it holds,
         every per-session cost below is being priced under the SAME
         validated rates.
      2. EV charging's share of the cycle's own kWh/cost - reported as
         context, not pass/fail (nothing on a real AGL invoice
         itemises this to compare against).
      3. A hard sanity BOUND, the one real pass/fail check here: the
         sum of every EV session's own real-AGL-meter import for its
         window (real_import_kwh) can never exceed the cycle's own
         total real household import - every session's window sits
         INSIDE the cycle, so violating this would mean a real bug
         (double-counted or wrongly-dated session windows), not
         anything about solar usage. Skipped (not "PASS", not "FAIL",
         just not checked) whenever the cycle's own usage master
         coverage is 0 kWh - AGL's usage master not covering that
         historical cycle at all is a data-gap, not a violation.

    Returns True if every cycle's bound (#3) held (or was skipped for
    lack of data), False if any cycle's bound was actually violated."""
    from power_cost_calculator import reconstruct_bill, sum_kwh, REAL_INVOICE_CYCLES

    periods = periods or load_rate_periods()
    all_sessions = load_evnex_deepdive_sessions(deepdive_dir=deepdive_dir)

    def _attrs(s):
        return (s.get("session", {}) or {}).get("attributes", {}) or {}

    all_sessions = [s for s in all_sessions if is_real_charging_session(_attrs(s))]

    def _session_local_date(s):
        epoch = iso_to_epoch(s.get("charging_started_utc"))
        if epoch is None:
            return None
        try:
            tz = ZoneInfo(s.get("timezone") or "Australia/Sydney")
        except Exception:
            tz = ZoneInfo("Australia/Sydney")
        return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz).date()

    all_ok = True
    for start, end, expected_total, label in REAL_INVOICE_CYCLES:
        log(f"=== {label}: {start} to {end} ===")
        bill = reconstruct_bill(start, end, master_path=master_path, periods=periods)
        bill_diff = abs(bill["total_incl_gst"] - expected_total)
        log(f"  whole-household reconstructed bill: ${bill['total_incl_gst']:.2f} incl GST "
            f"(invoice: ${expected_total:.2f}, diff ${bill_diff:.2f} - see power_cost_calculator.py "
            f"--self-test for the authoritative check on this figure)")

        cycle_sessions = [s for s in all_sessions if start <= (_session_local_date(s) or date.max) <= end]
        results = [evaluate_session(s, periods, master_path=master_path) for s in cycle_sessions]
        ev_kwh = sum(r["kwh_total"] for r in results)
        ev_cost_incl_gst = sum(
            r["pv_adjusted_cost_incl_gst"] if r.get("pv_adjusted_cost_incl_gst") is not None
            else (r["evnex_adjusted_cost_incl_gst"] if r["evnex_adjusted_cost_incl_gst"] is not None else r["base_cost_incl_gst"])
            for r in results
        )
        ev_import_sum = sum(r["real_import_kwh"] for r in results)
        cycle_import_kwh = sum_kwh(master_path, "import", start, end, provider=_POWER_PROVIDER)

        log(f"  {len(results)} EV charging session(s) in this cycle: {ev_kwh:.3f} kWh, "
            f"${ev_cost_incl_gst:.2f} incl GST computed (PVOutput/Evnex/base blended per-session)")
        if cycle_import_kwh > 0.01:
            share_pct = 100.0 * ev_kwh / cycle_import_kwh if cycle_import_kwh else 0.0
            bound_ok = ev_import_sum <= cycle_import_kwh + 0.01  # tiny float-rounding slack
            all_ok &= bound_ok
            log(f"  EV charging's share of cycle's real household import: {ev_kwh:.3f} / {cycle_import_kwh:.3f} kWh "
                f"({share_pct:.1f}%, informational only - AGL's invoice doesn't itemise this)")
            log(f"  sanity bound: EV sessions' own real-AGL-import sum ({ev_import_sum:.3f} kWh) <= cycle's whole-"
                f"household real import ({cycle_import_kwh:.3f} kWh) - {'PASS' if bound_ok else 'FAIL'}")
        else:
            log(f"  sanity bound: not checked - AGL usage master has 0 kWh of real import data for this cycle "
                f"(a data gap, not a violation - AGL's manual sync may not cover this historical cycle)")
        log("")

    log("ALL INVOICE CROSS-REFERENCE BOUNDS HELD" if all_ok else "INVOICE CROSS-REFERENCE FAILURE - see FAIL line(s) above")
    return all_ok


def main():
    parser = argparse.ArgumentParser(
        usage="""analyse_session_cost.py [--limit N | --all] [--since-days N] [--deepdive-dir DIR] [--usage-master CSV]
   or:  analyse_session_cost.py --session-id UUID [--deepdive-dir DIR] [--usage-master CSV]
   or:  analyse_session_cost.py --reconcile-invoices [--usage-master CSV]
""",
        description="""
NAME
    analyse_session_cost.py - per-session EV-charging cost under the real AGL Solar VIP tariff

DESCRIPTION
    Computes what a home-charging session actually cost under AGL's real dated
    Solar VIP rates - not Tessie's own "Cost"/"Cost Per kWh" columns (the car's
    own guess), and not a share of the monthly demand charge (that stays a
    separate whole-account number in power_demand_calculator.py /
    power_cost_calculator.py, never attributed to one session here). See the
    module docstring for the full tariff/cost-model rationale.

    --reconcile-invoices (REQ-071) cross-references AGL's real invoiced
    totals against the aggregate of this file's own computed per-session
    costs, for every cycle power_cost_calculator.py's own self-test
    already knows about - see reconcile_against_invoices()'s own
    docstring for exactly what is and isn't actually checked.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  analyse_session_cost.py --limit 10
  analyse_session_cost.py --since-days 30
  analyse_session_cost.py --all
  analyse_session_cost.py --session-id 3f9c1e2a-...-b7d4
  analyse_session_cost.py --reconcile-invoices
"""
    )
    parser.add_argument("--limit", type=int, default=20, help="Most recent N sessions to evaluate (default: 20, ignored with --all)")
    parser.add_argument("--since-days", type=int, help="Only sessions from the last N days")
    parser.add_argument("--session-id", help="Evaluate one specific Evnex session by its UUID")
    parser.add_argument("--all", action="store_true", help="Evaluate every session found (overrides --limit)")
    parser.add_argument("--deepdive-dir", help=f"Override the deep-dive JSON folder (default: {DEEPDIVE_DIR})")
    parser.add_argument("--usage-master", default=USAGE_MASTER, help=f"Override the AGL usage master CSV path (default: {USAGE_MASTER})")
    parser.add_argument("--reconcile-invoices", action="store_true",
                         help="Cross-reference AGL's real invoiced totals against the aggregate of computed per-session costs (REQ-071), instead of the normal per-session listing")
    args = parser.parse_args()

    if args.reconcile_invoices:
        ok = reconcile_against_invoices(deepdive_dir=args.deepdive_dir, master_path=args.usage_master)
        return 0 if ok else 1

    limit = None if (args.all or args.session_id) else args.limit
    sessions = load_evnex_deepdive_sessions(
        deepdive_dir=args.deepdive_dir, since_days=args.since_days, limit=limit, session_id=args.session_id
    )
    if not sessions:
        print("No matching Evnex deep-dive sessions found.")
        return 1

    # A session that delivered ~zero energy is a connection blip (charger
    # pairing/testing, a momentary plug/unplug, a start that never drew
    # power) rather than a real charge - see evnex_common.
    # is_real_charging_session()'s own docstring for how this was found
    # (a real charger swap producing 5 of these, plus 23 more already on
    # disk predating it). Excluded from every count/total below, but
    # never silently - the excluded count is always reported.
    def _attrs(s):
        return (s.get("session", {}) or {}).get("attributes", {}) or {}

    blips = [s for s in sessions if not is_real_charging_session(_attrs(s))]
    sessions = [s for s in sessions if is_real_charging_session(_attrs(s))]
    if not sessions:
        print(f"All {len(blips)} matching session(s) look like connection blips (0 kWh) - nothing real to evaluate.")
        return 0

    try:
        periods = load_rate_periods()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1

    results = [evaluate_session(s, periods, master_path=args.usage_master) for s in sessions]

    blip_note = f" ({len(blips)} connection blip(s) excluded - 0 kWh, not real charges)" if blips else ""
    print(f"=== {len(results)} home-charging session(s) ==={blip_note}\n")
    for r in results:
        print_session(r)

    total_kwh = sum(r["kwh_total"] for r in results)
    total_base = sum(r["base_cost"] for r in results)
    total_base_incl_gst = sum(r["base_cost_incl_gst"] for r in results)

    # REQ-071: blended total now prefers PVOutput-adjusted (real, measured
    # self-consumption) FIRST, Evnex-adjusted (a configured schedule, not a
    # live measurement) SECOND, and plain base cost only for a session with
    # neither - so every session is always covered, never silently dropped,
    # while always using the most direct measurement available for it.
    n_pv = sum(1 for r in results if r.get("pv_adjusted_cost") is not None)
    n_evnex_fallback = sum(1 for r in results if r.get("pv_adjusted_cost") is None and r["evnex_adjusted_cost"] is not None)
    n_base_only = len(results) - n_pv - n_evnex_fallback

    def _best_cost(r, incl_gst):
        key_pv = "pv_adjusted_cost_incl_gst" if incl_gst else "pv_adjusted_cost"
        key_evnex = "evnex_adjusted_cost_incl_gst" if incl_gst else "evnex_adjusted_cost"
        key_base = "base_cost_incl_gst" if incl_gst else "base_cost"
        if r.get(key_pv) is not None:
            return r[key_pv]
        if r.get(key_evnex) is not None:
            return r[key_evnex]
        return r[key_base]

    total_blended = sum(_best_cost(r, incl_gst=False) for r in results)
    total_blended_incl_gst = sum(_best_cost(r, incl_gst=True) for r in results)
    n_conflict = sum(1 for r in results if r["conflict"])
    n_pv_agl_conflict = sum(1 for r in results if r.get("pv_agl_conflict"))

    print("=== Totals ===")
    print(f"  {len(results)} session(s), {total_kwh:.3f} kWh total{blip_note}")
    print(f"  base cost (100% grid, every session)                         : ${total_base:.2f} ex GST -> ${total_base_incl_gst:.2f} incl GST")
    print(f"  blended best estimate (PVOutput split for {n_pv}/{len(results)}, Evnex fallback for {n_evnex_fallback},")
    print(f"                         base cost for the remaining {n_base_only})       : ${total_blended:.2f} ex GST -> ${total_blended_incl_gst:.2f} incl GST")
    if n_conflict:
        print(f"  ⚠️  {n_conflict} session(s) flagged with a solar-claim/real-import conflict (Evnex vs AGL) - see above")
    if n_pv_agl_conflict:
        print(f"  ⚠️  {n_pv_agl_conflict} session(s) flagged with a PVOutput/AGL real-import cross-source conflict - see above")
    return 0


if __name__ == "__main__":
    main()
