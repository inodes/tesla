"""
lib/session_cost.py - per-session EV-charging cost under the real AGL
Solar VIP tariff (REQ-154: moved here from the old standalone
analyse_session_cost.py script, folded into analyse_charging.py per the
user's own explicit steer - "why would I run analyse_session_cost.py by
passing in a session if I can just run ./analyse_charging.py to select a
session then have session_cost built in" - this file's substance is
otherwise UNCHANGED.

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

  2. "<PV provider>-adjusted" (REQ-071, 2026-09-15, PRIMARY; REQ-140,
     2026-09-25, migrated from PVOutput to providers_pv/SolarEdge -
     PVOutput on this account turned out to be a pure relay of this
     SAME SolarEdge account's own API, confirmed both by near-identical
     historical readings and by the user's own PVOutput "Edit System"
     settings screenshot, not an independent measurement) - splits the
     session's own kWh by a real household solar-self-consumption
     fraction measured directly from the active PV provider's own real
     per-interval generation/consumption/self-consumption meters
     (lib/pv_history_db.py's pv_window_split(), summed at 15-minute
     resolution, not just from day-level totals) for the session's own
     window. This is the user's own explicit steer (AGENTS.local.md
     REQ-071): of the three data sources this repo integrates, Evnex
     only sees the car's own usage plus a single CT clamp (good enough
     for ITS OWN solar% estimate, but see #3 below for why that
     estimate isn't a live measurement); AGL only sees import/export
     and is manual-sync besides; the PV provider alone can MEASURE -
     not infer - real household self-consumption. Same opportunity-cost
     solar valuation as #3 below (tier-1 rate, GST-free). Falls back to
     #3, then #1, whenever PV provider data isn't available or doesn't
     cover the window well (see evaluate_session()'s own pv_coverage
     handling).

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
cross-check (REQ-071, pv_agl_conflict) compares the PV provider's own
real import figure for the window against AGL's - two independent real
meters for the same physical quantity that should roughly agree; a
material mismatch there is a data-quality flag (clock drift, an
un-synced PV provider day, a stale AGL export), not a claim about
solar usage. One confirmed,
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

Deliberately NOT attempted here (left for a future finer-grained pass,
per resources/PVOUTPUT_API.md's "Fit with existing AGL/solar work" section):
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

Library usage (REQ-154): this file has no CLI of its own any more - it is imported by analyse_charging.py, which is the only supported entry point for everything charging-related. Batch AGL-invoice cross-reference: `./analyse_charging.py --reconcile-invoices`. Per-session cost breakdown: `./analyse_charging.py --inspect <#/date>` (shows this automatically for a home Evnex session - see get_energy_source_crosscheck() there, which calls evaluate_session() below - no separate flag needed).
"""

import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
from providers_power import get_power_provider  # noqa: E402
from power_demand_calculator import load_rate_periods, rate_for_date  # noqa: E402
from lib.power_usage_db import get_usage_db_connection, resolve_usage_db_path, iter_rows  # noqa: E402
from lib.evnex_common import is_real_charging_session  # noqa: E402
from lib.tessie_api_common import resolve_tessie_data_home, load_config  # noqa: E402
import lib.charge_paths as CP  # noqa: E402
from providers_pv import get_pv_provider  # noqa: E402
from lib.pv_history_db import get_pv_db_connection, pv_window_split  # noqa: E402
from lib.pvoutput_common import pv_agl_import_conflict  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

DEEPDIVE_DIR = os.path.join(resolve_tessie_data_home(), "charges", "deepdive")
_POWER_PROVIDER = get_power_provider()
DATA_HOME = _POWER_PROVIDER.resolve_data_home()

# REQ-071 (2026-09-15, PVOutput-based originally)/REQ-140 (2026-09-25,
# migrated to providers_pv/SolarEdge - PVOutput turned out to be a pure
# relay of this SAME SolarEdge account's own API, not an independent
# measurement, see AGENTS.local.md's REQ-140 entry): the active PV
# provider's credentials and a single persistent pv_history.db
# connection, resolved/opened once at import time (same "load once,
# reuse everywhere" pattern as DATA_HOME above) and shared across every
# evaluate_session() call in one batch run - lib/pv_history_db.py's own
# on-disk cache means a --all run over hundreds of sessions that share
# the same handful of calendar days only ever fetches each day once,
# same intent as the old module-level _PV_DAY_CACHE this replaces, now
# handled by the persistent SQLite cache instead of an in-memory dict.
_PV_PROVIDER = get_pv_provider()
_PV_CFG = load_config()
_PV_KEY, _PV_ACCOUNT_ID = _PV_PROVIDER.resolve_credentials(_PV_CFG)
_PV_CONN = get_pv_db_connection()

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
    for path in CP.glob_files(deepdive_dir, "*_Evnex_Home*.json"):
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


# REQ-158 (2026-09-28): Evnex has no vehicle/VIN/driver field at all, so
# whether a given session was even THIS car charging is otherwise a pure
# assumption - watermark-gated "no matching Tessie charge, AND sync proven
# complete through this window" detection (get_tessie_match_status()
# below), a persisted ignore list once the user confirms a session wasn't
# theirs (this file), a batch review command (review_unmatched_evnex_
# sessions()), and an inline confirmation prompt during --inspect
# (analyse_charging.py) together replace the "no Tessie match = not your
# car" guess the user explicitly flagged as unsafe.
OTHER_VEHICLE_IGNORE_PATH_NAME = ".evnex_other_vehicle_ids.json"


def load_other_vehicle_ids(deepdive_dir=None):
    """2026-09-28: session ids the user has explicitly confirmed were
    NOT charged by their own Tesla - Evnex has no vehicle/VIN/driver
    field at all (confirmed empirically: every real session's own
    authorizationMethod is "NoTokenRequired", no RFID/token config
    exists on this account), so the only way to know is asking, once,
    then remembering the answer - same shape/reasoning as REQ-153's own
    .evnex_blip_ids.json (sync_api_evnex.py), just a different reason
    code. NEVER auto-populated - only a human confirmation (via
    analyse_charging.py's --review-unmatched or the inline --inspect
    prompt) writes to this file. Sessions here are excluded from
    reconcile_against_invoices()'s own aggregate and flagged rather
    than silently priced as the user's own EV charging cost."""
    deepdive_dir = deepdive_dir or DEEPDIVE_DIR
    path = os.path.join(deepdive_dir, OTHER_VEHICLE_IGNORE_PATH_NAME)
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("ids") or [])
    except Exception:
        return set()


def save_other_vehicle_ids(ids, deepdive_dir=None):
    """Overwrites the other-vehicle-ignore file with the full current
    set - small (a handful of UUIDs at most), so a full rewrite is
    simpler and safer than an append-only log, matching
    save_ignored_blip_ids()'s own convention."""
    deepdive_dir = deepdive_dir or DEEPDIVE_DIR
    os.makedirs(deepdive_dir, exist_ok=True)
    path = os.path.join(deepdive_dir, OTHER_VEHICLE_IGNORE_PATH_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ids": sorted(ids)}, f, indent=2)


def sync_completeness_status(local_end, tz, repo_root=None):
    """Whether Tessie's own charge data is KNOWN to cover local_end yet
    (get_charges_sync_watermark()'s real last_synced_to watermark from
    resources/api_cache/charges.json - see that function's own
    docstring for why this check exists at all: absence of a matching
    Tessie charge is only meaningful evidence once sync is PROVABLY
    caught up past this session's own window, otherwise a sync lag is
    indistinguishable from a different vehicle). `tz` must be the SAME
    zoneinfo the caller used to build local_end (evaluate_session()
    already resolves this per-session) - re-deriving a timezone here
    independently would silently misconvert the comparison.

    Returns True (covered - a no-match here is meaningful), False (not
    yet covered - a no-match is inconclusive, not evidence of anything),
    or None (no watermark at all yet, e.g. sync has never run - also
    inconclusive)."""
    from lib.charge_cost_model import get_charges_sync_watermark
    repo_root = repo_root or os.path.dirname(_TOOLS_DIR)
    watermark = get_charges_sync_watermark(repo_root)
    if watermark is None or local_end is None:
        return None
    end_epoch = local_end.replace(tzinfo=tz).timestamp()
    # B6 (2026-09-30 review): the watermark is when the fetch ran, not proof
    # Tessie had finalised a charge that ended minutes earlier - allow the
    # same 10-minute grace analyse_charging uses (CHARGE_FINALIZE_GRACE_SECONDS).
    return end_epoch + 600 < watermark


def parse_agl_dt(s):
    return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M:%S %p")


_agl_index_cache = None


def load_agl_index(data_home):
    """{half_hour_start_naive_datetime: {"import": kWh, "solar_export": kWh}}
    - one pass through the usage database (lib/power_usage_db.py),
    cached for the life of this process so every session's lookup
    afterward is O(session length / 30 min) instead of re-querying the
    whole (~200k row) table each time. REQ-115: replaces the old
    single-pass agl_usage_master.csv scan - same shape, same cache."""
    global _agl_index_cache
    if _agl_index_cache is not None:
        return _agl_index_cache
    index = {}
    if not os.path.isfile(resolve_usage_db_path(_POWER_PROVIDER, data_home)):
        _agl_index_cache = index
        return index
    conn = get_usage_db_connection(_POWER_PROVIDER, data_home)
    try:
        for row in iter_rows(conn):
            role = row["Role"]
            if role not in ("import", "solar_export"):
                continue
            dt = parse_agl_dt(row["StartDate"])
            bucket = index.setdefault(dt, {})
            bucket[role] = bucket.get(role, 0.0) + float(row["kWh"])
    finally:
        conn.close()
    _agl_index_cache = index
    return index


def half_hour_floor(dt):
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def real_agl_overlap(data_home, local_start, local_end):
    """(import_kwh, export_kwh, buckets_found, buckets_missing) - the
    REAL whole-household AGL smart-meter total for every half-hour
    bucket overlapping [local_start, local_end) (naive Sydney-local
    datetimes). Whole-bucket sums, not prorated to the exact overlap
    fraction - fine for the short (well under an hour) sessions this
    dataset actually has; would need prorating for anything longer.
    `buckets_missing` counts buckets with no AGL data at all (outside
    an exported date range, or in the known 2022-05-31 to 2024-09-07
    gap - see AGENTS.md REQ-031) - never silently treated as zero."""
    index = load_agl_index(data_home)
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


_tessie_home_charges_cache = None


def load_tessie_home_charges(tessie_dir=None):
    """Real Tessie charges_master.csv rows for the Home network only,
    parsed once and cached for the life of this process - ground truth
    for what TESSIE ITSELF considers one charge (its own precise
    Started/Ended At window, its own Energy Used (kWh), and its own
    reported Cost/Cost Per kWh for direct comparison).

    User's own correction (2026-09-26): Evnex's chargingStarted/
    chargingStopped window is NOT the same thing as "when Tessie
    considers this a charge" - confirmed live, a real Evnex session
    reporting 176 minutes/7.239 kWh of continuous "charging" had only
    ONE matching Tessie charge record covering just 69 of those minutes
    and 6.88 kWh; Tessie's own charging state machine didn't consider
    the rest of that plugged-in window (breaks in current flow, e.g.
    solar-diversion cycling pausing/resuming) to be part of any charge
    at all. This is the fix: scope cost calculations to Tessie's own
    window/kWh via match_tessie_charges(), not Evnex's broader one."""
    global _tessie_home_charges_cache
    if _tessie_home_charges_cache is not None:
        return _tessie_home_charges_cache
    tessie_dir = tessie_dir or resolve_tessie_data_home()

 # 2026-09-28: found via the new tessie_match_status watermark check -
 # this used to require Saved Location == "Home" EXACTLY, which
 # silently dropped 178 of this account's real 244 home charges (the
 # Tessie place was renamed from "1108 Victoria Rd" to "Home" at some
 # point; everything charged before that rename has the OLD label and
 # was being excluded here). Same class of bug BUG-102 already fixed
 # for is_home_place() elsewhere (analyse_drives.py) - a place's NAME
 # is not a stable "is this home" signal, only places.json's own
 # authoritative type=="home" field (or, failing that, the literal
 # string "home") is. Reusing that same hardened function here rather
 # than re-deriving a second, now-proven-fragile copy of the same check.
    from lib.drives.helpers import is_home_place, haversine_distance_m
    try:
        from places_manager import load_places  # B6: import failure must not break every caller
        places = load_places()
    except Exception:
        places = {}
    # B6: also accept a charge by GPS inside the Home radius, so a blank or
    # stale "Saved Location" label can't hide a real home charge (which
    # then showed up as "was this your Tesla?").
    home_fences = []
    for _p in (places or {}).values():
        if isinstance(_p, dict) and _p.get("type") == "home":
            try:
                home_fences.append((float(_p["lat"]), float(_p["lon"]), float(_p.get("radius_m") or 100)))
            except (KeyError, TypeError, ValueError):
                pass

    def _row_at_home(row):
        if is_home_place(row.get("Saved Location"), places_dict=places):
            return True
        try:
            lat, lon = float(row.get("Latitude")), float(row.get("Longitude"))
        except (TypeError, ValueError):
            return False
        return any(haversine_distance_m(lat, lon, a, b) <= r for a, b, r in home_fences)

    rows = []
    for candidate in (os.path.join(tessie_dir, "charges", "charges_master.csv"),
                      os.path.join(tessie_dir, "charges_master.csv")):
        if not os.path.isfile(candidate):
            continue
        with open(candidate, "r", encoding="utf-8-sig", newline="") as f:
            import csv as _csv
            for row in _csv.DictReader(f):
                if not _row_at_home(row):
                    continue
                try:
                    started = datetime.strptime(row["Started At (Local)"].strip()[:16], "%Y-%m-%d %H:%M")
                    ended = datetime.strptime(row["Ended At (Local)"].strip()[:16], "%Y-%m-%d %H:%M")
                    kwh = float(row.get("Energy Used (kWh)") or 0.0)
                except (ValueError, KeyError, TypeError):
                    continue
                rows.append({
                    "started": started,
                    "ended": ended,
                    "kwh": kwh,
                    "tessie_cost": float(row["Cost"]) if row.get("Cost") not in (None, "") else None,
                    "tessie_cost_per_kwh": float(row["Cost Per kWh"]) if row.get("Cost Per kWh") not in (None, "") else None,
                })
        break
    _tessie_home_charges_cache = rows
    return rows


def match_tessie_charges(local_start, local_end, tessie_rows, tolerance_min=15):
    """Every real Tessie charges_master.csv row (Home only) that
    overlaps [local_start, local_end) - Evnex's own reported plug-in
    window - or starts within tolerance_min of it (mirrors
    analyse_charging.py's own match_detailed_charge() tolerance).
    Usually one, can be zero (Tessie considered none of this window a
    real charge) or more than one (the car paused and resumed drawing
    current more than once within the same plug-in session, and Tessie
    logged each resumption as its own separate charge record)."""
    tol_seconds = tolerance_min * 60
    matches = [
        row for row in tessie_rows
        if (row["started"] < local_end and row["ended"] > local_start)
        or abs((row["started"] - local_start).total_seconds()) <= tol_seconds
    ]
    matches.sort(key=lambda r: r["started"])
    return matches


def cumulative_solar_export_before(local_dt):
    """Real cumulative SolarEdge export (kWh) for local_dt's own
    calendar day, from local midnight up to local_dt. The AGL tier-1
    daily feed-in cap (10 kWh/day) is a running total FOR THE WHOLE DAY,
    not per-session - a session's own marginal solar kWh needs to know
    how much of that day's cap earlier (unrelated) export already used
    up before this session even started, to correctly value the
    session's OWN share as an opportunity cost (user's own explicit
    ask, 2026-09-26: "the opportunity cost needs to be calculated on
    which of the feed in tariffs would have been received during that
    charging session"). Returns None if PV provider data isn't
    available - same graceful-degradation shape as pv_window_split()
    elsewhere in this file."""
    if not (_PV_KEY and _PV_ACCOUNT_ID):
        return None
    midnight = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if local_dt <= midnight:
        return 0.0
    try:
        split = pv_window_split(_PV_CONN, midnight, local_dt, _PV_PROVIDER, _PV_KEY, _PV_ACCOUNT_ID)
    except Exception:
        return None
    return split.get("export_kwh")


def evaluate_tessie_charge(tessie_row, periods, data_home=DATA_HOME):
    """The real cost of exactly what TESSIE ITSELF considers one charge
    - its own precise Started/Ended At window and its own Energy Used
    (kWh) (user's ask #1), priced with the tier-1/tier-2 feed-in split
    correctly anchored to how much of that CALENDAR DAY's 10kWh cap was
    already used up before this specific charge started, rather than a
    flat single-tier assumption (user's ask #2)."""
    local_start = tessie_row["started"]
    local_end = tessie_row["ended"]
    kwh_total = tessie_row["kwh"]

    period = rate_for_date(local_start.date(), periods)
    usage_rate, usage_rate_breakdown = _POWER_PROVIDER.usage_rate_for_window(period, local_start, local_end)
    tier1_rate = period["solar_feed_in_tier1_rate_per_kwh"]
    GST_MULTIPLIER = 1.10

    base_cost = kwh_total * usage_rate
    base_cost_incl_gst = base_cost * GST_MULTIPLIER

    pv_split = {"solar_fraction": None, "coverage": 0.0,
                "error": f"no {_PV_PROVIDER.display_name} credentials configured"}
    if _PV_KEY and _PV_ACCOUNT_ID:
        try:
            pv_split = pv_window_split(_PV_CONN, local_start, local_end, _PV_PROVIDER, _PV_KEY, _PV_ACCOUNT_ID)
        except Exception as e:
            pv_split = dict(pv_split)
            pv_split["error"] = str(e)

    real_cost = real_cost_incl_gst = None
    solar_kwh = grid_kwh = None
    tier1_kwh = tier2_kwh = None
    tier2_rate = None
    cumulative_before = cumulative_solar_export_before(local_start)
    used_flat_tier1_fallback = False

    if pv_split.get("solar_fraction") is not None and kwh_total > 0:
        solar_kwh = kwh_total * pv_split["solar_fraction"]
        grid_kwh = kwh_total - solar_kwh
        grid_charge_ex_gst = grid_kwh * usage_rate
        if cumulative_before is not None:
            tier1_kwh, tier2_kwh, tier2_rate = _POWER_PROVIDER.solar_tier_split_kwh(cumulative_before, solar_kwh)
            solar_credit_component = tier1_kwh * tier1_rate + tier2_kwh * (tier2_rate or 0.0)
        else:
 # No cumulative-export figure available for earlier that day
 # (PV data gap) - fall back to the old flat tier-1-only
 # assumption rather than silently dropping the figure, but say
 # so explicitly so this isn't mistaken for a real tiered result.
            solar_credit_component = solar_kwh * tier1_rate
            used_flat_tier1_fallback = True
        real_cost = grid_charge_ex_gst + solar_credit_component
        real_cost_incl_gst = grid_charge_ex_gst * GST_MULTIPLIER + solar_credit_component

    return {
        "local_start": local_start,
        "local_end": local_end,
        "duration_min": round((local_end - local_start).total_seconds() / 60.0, 1),
        "kwh_total": kwh_total,
        "usage_rate": usage_rate,
        "usage_rate_breakdown": usage_rate_breakdown,
        "tier1_rate": tier1_rate,
        "base_cost": base_cost,
        "base_cost_incl_gst": base_cost_incl_gst,
        "pv_solar_fraction": pv_split.get("solar_fraction"),
        "pv_coverage": pv_split.get("coverage", 0.0),
        "pv_error": pv_split.get("error"),
        "solar_kwh": solar_kwh,
        "grid_kwh": grid_kwh,
        "cumulative_export_before_kwh": cumulative_before,
        "tier1_kwh": tier1_kwh,
        "tier2_kwh": tier2_kwh,
        "tier2_rate": tier2_rate,
        "used_flat_tier1_fallback": used_flat_tier1_fallback,
        "real_cost": real_cost,
        "real_cost_incl_gst": real_cost_incl_gst,
        "tessie_cost": tessie_row["tessie_cost"],
        "tessie_cost_per_kwh": tessie_row["tessie_cost_per_kwh"],
    }


def find_matching_evnex_session(local_start, local_end, evnex_sessions=None, pad_minutes=2):
    """Find the raw Evnex deep-dive session (as load_evnex_deepdive_sessions()
    returns it, with its own "_file" key) whose own charging window
    overlaps [local_start, local_end] - a Tessie-shaped session's own
    naive-local start/end (e.g. a charges_master.csv row's "datetime"
    and "datetime" + "duration_mins") - by WINDOW OVERLAP, not by how
    close the two start times are.

    This is the same interval-overlap pattern analyse_charging.py's
    get_energy_source_crosscheck() and _matching_evnex_json_stamp()
    already use inline (2026-09-28: extracted here as the one shared
    implementation, so a caller working from a Tessie-shaped session -
    like the inline --inspect confirmation prompt - can resolve to the
    matching raw Evnex session without duplicating this loop a third
    time). Real reason window-overlap matters, not start-time proximity:
    solar diversion can cycle the car's charging current on and off as
    export rises and falls, so Tessie logs each separate start/stop
    "charging" event as its OWN short master-CSV row - a 4-7min, <1kWh
    Tessie blip can sit entirely INSIDE a much longer real Evnex session
    that started up to an hour earlier (AGENTS.md REQ-050).

    Returns the matching raw Evnex session dict, or None if nothing
    overlaps."""
    if evnex_sessions is None:
        evnex_sessions = load_evnex_deepdive_sessions()
    pad = timedelta(minutes=pad_minutes)
    for es in evnex_sessions:
        es_start_epoch = iso_to_epoch(es.get("charging_started_utc"))
        if es_start_epoch is None:
            continue
        es_end_epoch = iso_to_epoch(es.get("charging_stopped_utc")) or es_start_epoch
        try:
            es_tz = ZoneInfo(es.get("timezone") or "Australia/Sydney")
        except Exception:
            es_tz = ZoneInfo("Australia/Sydney")
        es_start_dt = datetime.fromtimestamp(es_start_epoch, tz=timezone.utc).astimezone(es_tz).replace(tzinfo=None)
        es_end_dt = datetime.fromtimestamp(es_end_epoch, tz=timezone.utc).astimezone(es_tz).replace(tzinfo=None)
        if es_start_dt <= local_end + pad and es_end_dt >= local_start - pad:
            return es
    return None


def get_tessie_match_status(local_start, local_end, tz, evnex_session_id, tessie_rows=None, tessie_matches=None):
    """Cheap, cache-independent classification of whether this Evnex
    session's own vehicle-charging attribution is trustworthy - Evnex
    reports no vehicle/VIN at all, so a no-match against Tessie's own
    charge history is the only available signal, and it's only
    meaningful once sync coverage is PROVEN to reach past this
    session's window (sync_completeness_status() - see its docstring:
    the user's own correction was that a sync lag must never be
    mistaken for "another car"). Deliberately does NOT touch PV/AGL
    data or any on-disk cost cache - callers like the --review-unmatched
    batch command and the inline --inspect prompt need this to be fast
    and always fresh, never dependent on a possibly-stale
    get_energy_source_crosscheck() cache file that predates this field.

    Returns one of: "matched" (a real Tessie charge overlaps - normal),
    "confirmed_other_vehicle" (this session id is in the user's own
    persisted other_vehicle_ids.json - asked once, remembered from then
    on), "unconfirmed_no_match" (no match, sync proven complete through
    this window - worth asking about), or "sync_incomplete" (no match,
    but sync hasn't caught up yet - not evidence of anything)."""
    if tessie_matches is None:
        tessie_rows = tessie_rows if tessie_rows is not None else load_tessie_home_charges()
        tessie_matches = match_tessie_charges(local_start, local_end, tessie_rows)
    if tessie_matches:
        return "matched"
    if evnex_session_id in load_other_vehicle_ids():
        return "confirmed_other_vehicle"
    sync_covers_window = sync_completeness_status(local_end, tz)
    return "unconfirmed_no_match" if sync_covers_window else "sync_incomplete"


def get_tessie_match_status_for_session(session, tessie_rows=None):
    """Convenience wrapper for a raw Evnex session dict (as loaded by
    load_evnex_deepdive_sessions()) - does just enough of evaluate_
    session()'s own start/end/timezone parsing to call
    get_tessie_match_status() above, without any of that function's
    PV/AGL/cost computation."""
    start_epoch = iso_to_epoch(session.get("charging_started_utc"))
    end_epoch = iso_to_epoch(session.get("charging_stopped_utc")) or start_epoch
    if start_epoch is None:
        return None
    zone = session.get("timezone") or "Australia/Sydney"
    try:
        tz = ZoneInfo(zone)
    except Exception:
        tz = ZoneInfo("Australia/Sydney")
    local_start = datetime.fromtimestamp(start_epoch, tz=timezone.utc).astimezone(tz).replace(tzinfo=None)
    local_end = datetime.fromtimestamp(end_epoch, tz=timezone.utc).astimezone(tz).replace(tzinfo=None)
    if local_end <= local_start:
        local_end = local_start + timedelta(minutes=1)
    return get_tessie_match_status(local_start, local_end, tz, session.get("evnex_session_id"), tessie_rows=tessie_rows)


def evaluate_session(session, periods, data_home=DATA_HOME):
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
    usage_rate, usage_rate_breakdown = _POWER_PROVIDER.usage_rate_for_window(period, local_start, local_end)
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

    import_kwh, export_kwh, buckets_found, buckets_missing = real_agl_overlap(data_home, local_start, local_end)

    conflict = (
        solar_pct is not None
        and solar_pct >= CONFLICT_SOLAR_PCT_THRESHOLD
        and kwh_total > 0
        and import_kwh >= CONFLICT_IMPORT_RATIO * kwh_total
    )

 # REQ-071/REQ-140: PV-provider-primary solar split - the active
 # provider's real per-interval generation/consumption/self-
 # consumption meters for this session's own window (module-level
 # _PV_CONN's on-disk cache shares one fetch per calendar day across a
 # whole batch run). Never fatal to any of the figures above if it
 # fails (missing credentials, network egress, rate limit, an
 # unexpected response shape) - evnex_adjusted_cost above stays
 # available as the fallback.
    pv_split = {
        "generation_kwh": None, "consumption_kwh": None, "self_consumed_kwh": None,
        "export_kwh": None, "import_kwh": None, "solar_fraction": None, "coverage": 0.0,
        "error": f"no {_PV_PROVIDER.display_name} credentials configured",
    }
    if _PV_KEY and _PV_ACCOUNT_ID:
        try:
            pv_split = pv_window_split(_PV_CONN, local_start, local_end, _PV_PROVIDER, _PV_KEY, _PV_ACCOUNT_ID)
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

 # User's own correction (2026-09-26): everything above prices EVNEX's
 # own chargingStarted/chargingStopped window - useful context, but NOT
 # what Tessie itself bills as "a charge" (ask #1). This attaches the
 # Tessie-scoped view alongside it, additively - nothing above changes,
 # so reconcile_against_invoices()'s existing use of this dict's
 # top-level fields is unaffected.
    tessie_home_rows = load_tessie_home_charges()
    tessie_matches = match_tessie_charges(local_start, local_end, tessie_home_rows)
    tessie_charges = [evaluate_tessie_charge(m, periods, data_home=data_home) for m in tessie_matches]
    match_status = get_tessie_match_status(local_start, local_end, tz, session.get("evnex_session_id"),
                                            tessie_rows=tessie_home_rows, tessie_matches=tessie_matches)

    return {
        "evnex_session_id": session.get("evnex_session_id"),
        "tessie_charges": tessie_charges,
        "tessie_match_status": match_status,
        "local_start": local_start,
        "local_end": local_end,
        "duration_min": round((local_end - local_start).total_seconds() / 60.0, 1),
        "kwh_total": kwh_total,
        "period_effective_from": period["effective_from"],
        "usage_rate": usage_rate,
        "usage_rate_breakdown": usage_rate_breakdown,
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
    status = r.get("tessie_match_status")
    if status == "confirmed_other_vehicle":
        print(f"   \033[91m⚠️  CONFIRMED: not your vehicle - excluded from invoice reconciliation\033[0m")
    elif status == "unconfirmed_no_match":
        print(f"   \033[93m⚠️  No matching Tessie charge (sync confirmed complete through this window) - may not be your vehicle\033[0m")
    elif status == "sync_incomplete":
        print(f"   \033[90mℹ️  No matching Tessie charge yet, but sync hasn't caught up to this window - not conclusive\033[0m")
    print(f"   base cost (100% grid)                 : ${r['base_cost']:.4f} ex GST -> ${r['base_cost_incl_gst']:.4f} incl GST")
    if r.get("pv_adjusted_cost_incl_gst") is not None:
        pv_pct = r["pv_solar_fraction"] * 100.0
        cov_note = "" if r.get("pv_coverage", 1.0) >= 0.99 else f", {r['pv_coverage']*100:.0f}% window coverage"
        print(f"   {_PV_PROVIDER.display_name}-adjusted (PRIMARY, solar {pv_pct:.1f}% real measured{cov_note}) : "
              f"${r['pv_adjusted_cost']:.4f} ex GST -> ${r['pv_adjusted_cost_incl_gst']:.4f} incl GST")
    else:
        print(f"   {_PV_PROVIDER.display_name}-adjusted (PRIMARY)            : unavailable ({r.get('pv_error') or 'unknown reason'}) - falling back to Evnex/base below")
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
        print(f"   {_PV_PROVIDER.display_name} gen/cons (whole household, this window): {r['pv_generation_kwh']:.3f} kWh gen, {r['pv_consumption_kwh']:.3f} kWh cons -> "
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
        print(f"   ⚠️  CONFLICT: {_PV_PROVIDER.display_name}'s real import for this window ({r['pv_import_kwh']:.3f} kWh) disagrees with "
              f"AGL's real meter import ({r['real_import_kwh']:.3f} kWh) by >=30% - a data-quality issue (clock drift, "
              f"an un-synced PV day, a stale AGL export), not a solar-usage question. See REQ-071.")
    print_tessie_charges(r)
    print()


def print_tessie_charges(r):
    """The user's own ask #1: what did TESSIE ITSELF actually consider
    "a charge" within this Evnex-reported plug-in window, and what did
    it really cost - priced with the tier-1/tier-2 feed-in split
    correctly anchored per-charge (ask #2), not the Evnex-window figures
    printed above this."""
    tessie_charges = r.get("tessie_charges") or []
    if not tessie_charges:
        print(f"   ⚠️  Tessie itself logged NO charge record at all overlapping this window - Evnex reports "
              f"this whole span as charging, but Tessie's own charging state considered none of it a real charge.")
        return
    print(f"   --- Tessie's own charge record(s) for this window ({len(tessie_charges)}) ---")
    total_real_incl_gst = 0.0
    total_tessie_cost = 0.0
    any_real = False
    for tc in tessie_charges:
        ts = tc["local_start"].strftime("%H:%M")
        te = tc["local_end"].strftime("%H:%M")
        print(f"   [{ts} -> {te}, {tc['duration_min']:.1f}min, {tc['kwh_total']:.3f} kWh] Tessie's own charge record")
        if tc["real_cost_incl_gst"] is not None:
            any_real = True
            total_real_incl_gst += tc["real_cost_incl_gst"]
            solar_pct = (tc["solar_kwh"] / tc["kwh_total"] * 100.0) if tc["kwh_total"] else 0.0
            print(f"      Solar self-consumption (measured, this exact window): {solar_pct:.1f}% "
                  f"({tc['solar_kwh']:.3f} kWh solar / {tc['grid_kwh']:.3f} kWh grid)")
            if tc["used_flat_tier1_fallback"]:
                print(f"      Daily feed-in cap: cumulative export before this charge unavailable - "
                      f"assumed flat tier-1 (${tc['tier1_rate']}/kWh) for the whole solar share")
            else:
                print(f"      Daily feed-in cap: {tc['cumulative_export_before_kwh']:.3f} kWh already exported "
                      f"today before this charge started -> {tc['tier1_kwh']:.3f} kWh @ tier-1 (${tc['tier1_rate']}/kWh), "
                      f"{tc['tier2_kwh']:.3f} kWh @ tier-2 (${tc['tier2_rate']}/kWh)")
            print(f"      Real cost (tiered)   : ${tc['real_cost']:.4f} ex GST -> ${tc['real_cost_incl_gst']:.4f} incl GST")
        else:
            print(f"      Real cost: unavailable ({tc.get('pv_error') or 'no PV data for this window'})")
        if tc["tessie_cost"] is not None:
            total_tessie_cost += tc["tessie_cost"]
            per_kwh_note = f" (${tc['tessie_cost_per_kwh']}/kWh flat)" if tc["tessie_cost_per_kwh"] is not None else ""
            print(f"      Tessie's own reported cost: ${tc['tessie_cost']:.2f}{per_kwh_note}")
    if len(tessie_charges) > 1 or (any_real and tessie_charges[0]["tessie_cost"] is not None):
        diff = total_real_incl_gst - total_tessie_cost
        print(f"   Tessie-scoped total: ${total_real_incl_gst:.4f} incl GST (real, tiered) vs "
              f"${total_tessie_cost:.2f} (Tessie's own) - Tessie {'overstates' if diff < 0 else 'understates'} "
              f"by ${abs(diff):.2f}")


def confirm_other_vehicle(session_id, deepdive_dir=None):
    """Persist session_id as user-confirmed-not-mine. Shared by both the
    batch --review-unmatched command and the inline --inspect prompt so
    there's exactly one place this ever gets written."""
    ids = load_other_vehicle_ids(deepdive_dir)
    ids.add(session_id)
    save_other_vehicle_ids(ids, deepdive_dir)


def review_unmatched_evnex_sessions(deepdive_dir=None, interactive=True):
    """2026-09-28: batch triage for every Evnex session that has no
    matching Tessie charge AND sync is confirmed complete through its
    window (see evaluate_session()'s tessie_match_status / the module
    docstring on sync_completeness_status()) - the "may not be your
    vehicle" cases. A real connection blip (0 kWh, is_real_charging_
    session() == False) is excluded here same as everywhere else - it's
    not a vehicle-attribution question at all, just not a charge.

    interactive=False (non-tty) just lists them, same shape --flagged/
    display_flagged_deletions_menu uses elsewhere in this repo for a
    review queue. Deliberately uses the cheap get_tessie_match_status_
    for_session() rather than the full evaluate_session() - this needs
    to scan every real session in one pass without a live PV fetch per
    session."""
    sessions = load_evnex_deepdive_sessions(deepdive_dir=deepdive_dir)

    def _attrs(s):
        return (s.get("session", {}) or {}).get("attributes", {}) or {}

    sessions = [s for s in sessions if is_real_charging_session(_attrs(s))]
    tessie_rows = load_tessie_home_charges()

    unmatched = []
    for s in sessions:
        status = get_tessie_match_status_for_session(s, tessie_rows=tessie_rows)
        if status == "unconfirmed_no_match":
            unmatched.append(s)

    if not unmatched:
        print("✅ No unmatched Evnex sessions to review - every real session either has a matching "
              "Tessie charge, is already confirmed, or sync hasn't caught up to it yet.")
        return {"reviewed": 0, "confirmed_other": 0}

    print(f"\n{len(unmatched)} Evnex session(s) have no matching Tessie charge, with sync confirmed "
          f"complete through their window:\n")

    n_confirmed = 0
    for i, s in enumerate(unmatched, 1):
        local_dt = datetime.fromtimestamp(iso_to_epoch(s.get("charging_started_utc")), tz=timezone.utc)
        try:
            local_dt = local_dt.astimezone(ZoneInfo(s.get("timezone") or "Australia/Sydney"))
        except Exception:
            pass
        ts = local_dt.strftime("%Y-%m-%d %H:%M")
        attrs = _attrs(s)
        kwh = ((attrs.get("totalEnergyUsage") or {}).get("total") or 0.0) / 1000.0
        dur = attrs.get("totalChargingTime") or 0.0
        print(f"[{i}/{len(unmatched)}] {ts}  {dur:.0f}min  {kwh:.3f} kWh  (session {s.get('evnex_session_id')})")
        if not interactive or not sys.stdin.isatty():
            continue
        try:
            print("    Was this your Tesla?")
            print("      [y]es")
            print("      [n]o - flag as another vehicle")
            print("      [s]kip for now")
            ans = input("      > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if ans in ("n", "no"):
            confirm_other_vehicle(s.get("evnex_session_id"), deepdive_dir)
            n_confirmed += 1
            print("    -> flagged as another vehicle, will be excluded from now on.")
        elif ans in ("y", "yes"):
            print("    -> left as-is (still unconfirmed - Tessie may just not have logged it as a charge).")
        else:
            print("    -> skipped, will show up again next run.")

    print(f"\nDone: {n_confirmed} session(s) newly flagged as another vehicle, "
          f"{len(unmatched) - n_confirmed} left unconfirmed.")
    return {"reviewed": len(unmatched), "confirmed_other": n_confirmed}


def reconcile_against_invoices(deepdive_dir=None, data_home=DATA_HOME, periods=None, log=print):
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
         INSIDE the cycle, so violating this would mean a real issue
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
        bill = reconstruct_bill(start, end, data_home=data_home, periods=periods)
        bill_diff = abs(bill["total_incl_gst"] - expected_total)
        log(f"  whole-household reconstructed bill: ${bill['total_incl_gst']:.2f} incl GST "
            f"(invoice: ${expected_total:.2f}, diff ${bill_diff:.2f} - see power_cost_calculator.py "
            f"--self-test for the authoritative check on this figure)")

        cycle_sessions = [s for s in all_sessions if start <= (_session_local_date(s) or date.max) <= end]
        results = [evaluate_session(s, periods, data_home=data_home) for s in cycle_sessions]

 # 2026-09-28: a session confirmed by the user as NOT their vehicle
 # (see load_other_vehicle_ids()) still real-consumed real household
 # electricity - it stays IN the sanity bound below (that's checking
 # for double-counted/mis-dated sessions against the cycle's own real
 # import, not EV attribution) but is excluded from the "EV charging"
 # kWh/cost figures, which are specifically about the user's own EV.
        own_ev_results = [r for r in results if r.get("tessie_match_status") != "confirmed_other_vehicle"]
        n_other_vehicle = len(results) - len(own_ev_results)
        ev_kwh = sum(r["kwh_total"] for r in own_ev_results)
        ev_cost_incl_gst = sum(
            r["pv_adjusted_cost_incl_gst"] if r.get("pv_adjusted_cost_incl_gst") is not None
            else (r["evnex_adjusted_cost_incl_gst"] if r["evnex_adjusted_cost_incl_gst"] is not None else r["base_cost_incl_gst"])
            for r in own_ev_results
        )
        ev_import_sum = sum(r["real_import_kwh"] for r in results)
        cycle_import_kwh = sum_kwh(data_home, "import", start, end, provider=_POWER_PROVIDER)

        other_note = f", {n_other_vehicle} confirmed not-your-vehicle session(s) excluded" if n_other_vehicle else ""
        log(f"  {len(own_ev_results)} EV charging session(s) in this cycle: {ev_kwh:.3f} kWh, "
            f"${ev_cost_incl_gst:.2f} incl GST computed ({_PV_PROVIDER.display_name}/Evnex/base blended per-session){other_note}")
        n_unconfirmed = sum(1 for r in own_ev_results if r.get("tessie_match_status") == "unconfirmed_no_match")
        if n_unconfirmed:
            log(f"  ⚠️  {n_unconfirmed} session(s) this cycle have no matching Tessie charge (sync confirmed "
                f"complete through their window) and aren't yet confirmed either way - still counted above; "
                f"run --review-unmatched to triage")
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


