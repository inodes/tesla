"""
lib/supercharger_pricing.py - Tesla Supercharger AU per-kWh price
history + current-rate resolver, REQ-160 (2026-09-28), REQ-161
(2026-09-29), REQ-162 (2026-09-29).

User, on cost_per_100km_report.py's EV "Highway" line using the AGL HOME
usage rate: "Why is cost on highway so low. I think what is genuinely
required is using highway against Tesla Supercharging costs. You can
probably use the data we have to get the current average daytime cents
per kWh now But historically * January 2019 ($0.35/kWh) * Late January
2019 ($0.42/kWh) * October 2020 ($0.52/kWh)". Real-world driving pattern
behind this correction: home (AGL) charging for urban/around-town use,
Supercharging on a highway trip - so the "Highway" cost line should
price against what Supercharging actually costs, not the home rate.

REQ-161: the user's follow-up - "No variables should live in code" -
moved the published price-history data out of this file's own source
(it used to be a hardcoded PUBLISHED_RATE_HISTORY list here) into
charges/supercharger_rate_history.json in the account's data folder,
matching the AGL/agl_tariff_rates.json convention.

REQ-162: the user's next follow-up - "I would be good to get averages
from all superchargers moving forward and if they differ a lot,
recording it as a new supercharger_rate_history.json entry." This
changes two things:

1. This module now supports REAL rate REGIMES, not just one blended
   "current" average of every real session ever seen. Before REQ-162,
   any date on/after this account's very first real Supercharger session
   used ONE lifetime average of every real session, forever - fine while
   only one real price era exists, but wrong the moment a genuine second
   price change shows up in real data (it would blend the old and new
   eras into one smeared number, and the old era would never get
   preserved as its own dated history). detect_real_rate_regimes() below
   segments real sessions into distinct regimes wherever consecutive
   sessions' rates diverge by more than a tolerance; resolve_since_date()/
   current_regime_rate() then only average sessions belonging to the
   CURRENT (most recent, not-yet-closed-out) regime for "today's" rate,
   while an earlier regime that's already been written to
   charges/supercharger_rate_history.json (via sync_supercharger_rates.py
   - see that script) stays fixed at its own historical rate, exactly the
   way providers_power's own dated periods never retroactively change.

2. sync_supercharger_rates.py (new, sibling script to sync_agl_rates.py)
   is the tool that actually watches real session data over time and
   writes a new dated period into charges/supercharger_rate_history.json
   when a detected regime is genuinely new (diverges from the last
   recorded rate by more than the tolerance) or refines the file's
   existing still-open real-data period in place when it's the same
   regime with more sessions now. Report-only by default, --apply to
   write - run it periodically ("moving forward") to keep the history
   file catching up with real price changes as they happen.

Two real price sources, combined into one step-function history exactly
the way providers_power/power_agl.py's own rate_for_date() works (a
sorted list of dated periods, each held flat until the next one):

1. charges/supercharger_rate_history.json's own "periods" list - real,
   dated AU Supercharger price changes from published news coverage
   (deliberately NOT this account's own invoices - Tesla Supercharger AU
   pricing is a single NETWORK rate charged the same way to every
   customer at a given site/time, not billed per-account the way AGL
   is), PLUS (REQ-162) any real-account-data regimes
   sync_supercharger_rates.py has since written there (marked
   `"source": "real_account_data"`). Cross-checked against the user's
   own 3 anchor points, with the Oct-2020 figure independently
   corroborated by two separate sources - see that JSON file for the
   full source notes per period, including the one acknowledged gap (no
   published figure found for 2023-2025).

2. This account's OWN real Supercharger sessions (charges_master.csv,
   the same `Supercharger == "true"` check and the same "Cost Per kWh"
   column analyse_charging.py's own cost-patching code already reads) -
   authoritative, for the CURRENT still-open regime only (see point 1
   above), from the date that regime started onward, superseding the
   published/logged history entirely for that window. Only 6 real
   sessions exist so far (all 2026, one regime), but they show a real,
   internally-consistent split once grouped by local hour: 3 daytime
   (6am-6pm) sessions at $0.44-0.45/kWh, and 3 evening/night sessions all
   at exactly $0.59/kWh - not noise, so this module defaults to the
   daytime-only average per the user's own explicit "daytime" wording,
   rather than blending two genuinely different real price points
   together.
"""

import csv
import json
import os
import sys
from datetime import date, datetime

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from lib.tessie_api_common import resolve_tessie_data_home  # noqa: E402

RATE_HISTORY_FILENAME = os.path.join("charges", "supercharger_rate_history.json")

# REQ-162: the $/kWh divergence that counts as a genuine new price
# regime rather than ordinary site-to-site noise. This account's own
# real daytime sessions so far span $0.44-0.45/kWh (a ~$0.01 spread);
# every real, dated AU network price change this module has a source
# for jumped by at least $0.05/kWh at a time (0.35->0.42 = +0.07,
# 0.42->0.52 = +0.10, 0.52->0.57 = +0.05, 0.57->0.685 = +0.115). $0.03
# sits comfortably between those two - well above the observed noise
# floor, well below every real jump on record.
DEFAULT_REGIME_TOLERANCE_PER_KWH = 0.03


def load_published_rate_history(tessie_dir=None):
    """[{"effective_from": date, "rate_per_kwh": float, "note": str,
    "source": str|None, "session_count": int|None}, ...], oldest first -
    read from charges/supercharger_rate_history.json in the account's
    data folder (REQ-161: moved out of this module's own source code).
    Despite the name, this now also includes any real-account-data
    regimes sync_supercharger_rates.py has written there (REQ-162) - it
    is the file's complete step-function history, published research and
    real data both. Returns [] if the file doesn't exist yet, so callers
    degrade to "no history, real data only" rather than crashing."""
    data_home = tessie_dir or resolve_tessie_data_home()
    path = os.path.join(data_home, RATE_HISTORY_FILENAME)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    periods = []
    for p in raw.get("periods", []):
        periods.append({
            "effective_from": date.fromisoformat(p["effective_from"]),
            # None = a documented gap (no published source) - the chart shows
            # a break instead of carrying the previous rate forward (D5).
            "rate_per_kwh": float(p["rate_per_kwh"]) if p.get("rate_per_kwh") is not None else None,
            "note": p.get("note", ""),
            "source": p.get("source"),
            "session_count": p.get("session_count"),
        })
    periods.sort(key=lambda p: p["effective_from"])
    return periods


def load_real_supercharger_sessions(tessie_dir=None):
    """[(local_datetime, cost_per_kwh), ...] for every real Supercharger
    session in charges_master.csv - same `Supercharger == "true"` check
    and same "Cost Per kWh" field analyse_charging.py's own cost-
    patching code already reads (never re-derived via Cost/Energy
    Added - that field can legitimately differ from a simple division,
    e.g. membership credits - "Cost Per kWh" is Tessie's own reported
    per-session rate). Sorted oldest first. Empty list if the file or
    column doesn't exist yet."""
    data_home = tessie_dir or resolve_tessie_data_home()
    path = os.path.join(data_home, "charges", "charges_master.csv")
    if not os.path.isfile(path):
        return []
    sessions = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("Supercharger") or "").strip().lower() != "true":
                continue
            ts_raw = (row.get("Started At (Local)") or "").strip()
            if not ts_raw:
                continue
            ts_str = ts_raw.rsplit(" ", 1)[0]  # strip the trailing "AEST"/"AEDT" tz abbreviation
            try:
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            try:
                rate = float(row.get("Cost Per kWh") or 0)
            except (TypeError, ValueError):
                continue
            if rate <= 0:
                continue
            sessions.append((dt, rate))
    sessions.sort(key=lambda s: s[0])
    return sessions


def daytime_only_sessions(sessions, daytime_start_hour=6, daytime_end_hour=18):
    """Filters a [(datetime, rate), ...] list to local hours in
    [daytime_start_hour, daytime_end_hour) - the same window this
    module's "daytime" default has used since REQ-160."""
    return [(dt, r) for dt, r in sessions if daytime_start_hour <= dt.hour < daytime_end_hour]


def resolve_since_date(real_sessions, published_history, daytime_only=True,
                         tolerance=DEFAULT_REGIME_TOLERANCE_PER_KWH):
    """REQ-162: the date from which "the current regime" should be
    averaged live from real sessions, rather than read as a fixed
    historical rate off charges/supercharger_rate_history.json - the
    LATER of (a) the start of the most recent regime detect_real_rate_
    regimes() finds live in the real session data itself, and (b) the
    history file's own last recorded period's effective_from.

    Detecting live (rather than only trusting whatever
    sync_supercharger_rates.py has already logged) matters because a
    genuine new price regime can show up in real sessions BEFORE that
    script has next been run to log it - without live detection here,
    "today's" rate would keep blending the old and new regimes together
    in the meantime, exactly the REQ-162 problem this module exists to
    avoid. Once sync_supercharger_rates.py DOES log it, (b) catches up
    to (a) and they agree - this is what makes current_regime_rate()
    correct in both states. Returns None if there's no real session
    data at all yet."""
    if not real_sessions:
        return None
    day_sessions = daytime_only_sessions(real_sessions) if daytime_only else real_sessions
    regimes = detect_real_rate_regimes(day_sessions, tolerance=tolerance) if day_sessions else []
    latest_regime_start = regimes[-1]["effective_from"] if regimes else real_sessions[0][0].date()
    if published_history:
        return max(latest_regime_start, published_history[-1]["effective_from"])
    return latest_regime_start


def current_supercharger_rate(daytime_only=True, daytime_start_hour=6, daytime_end_hour=18,
                                real_sessions=None, tessie_dir=None, since=None):
    """(rate, sample_count) - the simple average of this account's own
    real Supercharger "Cost Per kWh" readings on/after `since` (every
    real session ever, if `since` is None - REQ-160's original,
    unwindowed behavior), restricted to local daytime hours by default
    per the user's own explicit "daytime" ask (see module docstring for
    why). Returns (None, 0) if there's no real data to average in that
    window."""
    if real_sessions is None:
        real_sessions = load_real_supercharger_sessions(tessie_dir)
    if since is not None:
        real_sessions = [(dt, r) for dt, r in real_sessions if dt.date() >= since]
    if daytime_only:
        real_sessions = daytime_only_sessions(real_sessions, daytime_start_hour, daytime_end_hour)
    if not real_sessions:
        return None, 0
    rates = [r for _, r in real_sessions]
    return sum(rates) / len(rates), len(rates)


def current_regime_rate(daytime_only=True, daytime_start_hour=6, daytime_end_hour=18,
                          real_sessions=None, published_history=None, tessie_dir=None):
    """(rate, sample_count, since_date) - REQ-162: the live average of
    real sessions in the CURRENT regime only (see resolve_since_date()),
    not this account's whole real-session lifetime. This is what "today's
    real Supercharger rate" should mean once more than one real regime
    exists; while only one regime exists (true so far) it equals
    current_supercharger_rate()'s own unwindowed average, since
    resolve_since_date() then just returns the first real session's own
    date. Returns (None, 0, since_date) if there's no real data in that
    window yet."""
    if real_sessions is None:
        real_sessions = load_real_supercharger_sessions(tessie_dir)
    if published_history is None:
        published_history = load_published_rate_history(tessie_dir)
    since = resolve_since_date(real_sessions, published_history, daytime_only=daytime_only)
    rate, n = current_supercharger_rate(
        daytime_only, daytime_start_hour, daytime_end_hour,
        real_sessions=real_sessions, since=since, tessie_dir=tessie_dir,
    )
    return rate, n, since


def detect_real_rate_regimes(sessions, tolerance=DEFAULT_REGIME_TOLERANCE_PER_KWH):
    """Segments a chronologically-sorted [(datetime, rate), ...] list
    (already restricted to whichever hour window the caller wants, e.g.
    daytime-only) into consecutive regimes: a new regime starts whenever
    a session's rate strays by more than `tolerance` $/kWh from the
    regime-so-far's running mean. Used by sync_supercharger_rates.py
    (REQ-162) to find genuine price changes in real session data instead
    of averaging every session ever into one number forever. Returns
    [{"effective_from": date, "rate_per_kwh": float, "session_count": int,
    "last_session_at": date}, ...], oldest first. [] if `sessions` is
    empty."""
    if not sessions:
        return []
    # 2026-09-30 review (D9): one odd session (e.g. an evening-priced $0.59
    # stop inside the 6am-6pm window) used to start a brand-new "regime" on
    # its own, and the next normal session started yet another. A new
    # regime now needs MIN_REGIME_SESSIONS consecutive sessions that agree
    # with each other; a lone outlier that's followed by a return to the
    # current rate is left out of every average.
    MIN_REGIME_SESSIONS = 2
    regimes_raw = []
    current = {"dates": [sessions[0][0].date()], "rates": [sessions[0][1]]}
    pending = []
    for dt, rate in sessions[1:]:
        running_mean = sum(current["rates"]) / len(current["rates"])
        if abs(rate - running_mean) <= tolerance:
            pending = []  # back in line - a lone outlier is dropped
            current["dates"].append(dt.date())
            current["rates"].append(rate)
            continue
        pending_mean = sum(r for _, r in pending) / len(pending) if pending else None
        if pending and abs(rate - pending_mean) <= tolerance:
            pending.append((dt, rate))
        else:
            pending = [(dt, rate)]
        if len(pending) >= MIN_REGIME_SESSIONS:
            regimes_raw.append(current)
            current = {"dates": [d.date() for d, _ in pending], "rates": [r for _, r in pending]}
            pending = []
    regimes_raw.append(current)
    return [
        {
            "effective_from": min(r["dates"]),
            "rate_per_kwh": round(sum(r["rates"]) / len(r["rates"]), 4),
            "session_count": len(r["rates"]),
            "last_session_at": max(r["dates"]),
        }
        for r in regimes_raw
    ]


def supercharger_rate_for_date(d, daytime_only=True, real_sessions=None, current_rate=None,
                                 tessie_dir=None, published_history=None):
    """The Supercharger $/kWh rate in effect on date `d` (a date
    object) - charges/supercharger_rate_history.json's own step function
    (published research AND any real-account-data regimes
    sync_supercharger_rates.py has logged there - REQ-161/162), with the
    CURRENT still-open regime (from resolve_since_date() onward) priced
    live off real sessions rather than whatever's on file, since that
    regime hasn't been closed out/logged yet - real data always wins
    over a fixed historical rate for its own regime's dates.

    For charting many dates, pass `real_sessions` (from a single
    load_real_supercharger_sessions() call) and `published_history`
    (from a single load_published_rate_history() call) so this isn't
    re-reading a file on every date; `current_rate`, if you already have
    it (e.g. from current_regime_rate()), skips recomputing the live
    average too, but must be the CURRENT-REGIME average (since
    resolve_since_date()'s cutoff), not an all-time one, or dates in an
    earlier, already-logged regime would wrongly get today's rate."""
    if real_sessions is None:
        real_sessions = load_real_supercharger_sessions(tessie_dir)
    if published_history is None:
        published_history = load_published_rate_history(tessie_dir)

    since = resolve_since_date(real_sessions, published_history, daytime_only=daytime_only)
    if since is not None and d >= since:
        if current_rate is None:
            current_rate, _n, _since = current_regime_rate(
                daytime_only, real_sessions=real_sessions,
                published_history=published_history, tessie_dir=tessie_dir,
            )
        if current_rate is not None:
            return current_rate

    rate = None
    for period in published_history:
        if period["effective_from"] <= d:
            rate = period["rate_per_kwh"]
        else:
            break
    return rate


if __name__ == "__main__":
    sessions = load_real_supercharger_sessions()
    print(f"{len(sessions)} real Supercharger session(s) found.")
    day_rate, day_n = current_supercharger_rate(daytime_only=True, real_sessions=sessions)
    night_rate, night_n = current_supercharger_rate(daytime_only=False, real_sessions=sessions)
    print(f"Current daytime average (all-time): ${day_rate:.3f}/kWh (n={day_n})" if day_rate else "No daytime sessions yet.")
    print(f"Current all-hours average (all-time): ${night_rate:.3f}/kWh (n={night_n})" if night_rate else "No sessions yet.")
    history = load_published_rate_history()
    print(f"{len(history)} rate period(s) loaded from {RATE_HISTORY_FILENAME}.")
    regime_rate, regime_n, since = current_regime_rate(real_sessions=sessions, published_history=history)
    print(f"Current REGIME daytime average (since {since}): ${regime_rate:.3f}/kWh (n={regime_n})" if regime_rate else f"No real sessions since {since}.")
    day_sessions = daytime_only_sessions(sessions)
    regimes = detect_real_rate_regimes(day_sessions)
    print(f"{len(regimes)} daytime regime(s) detected in real session data:")
    for r in regimes:
        print(f"  from {r['effective_from']} (last session {r['last_session_at']}): ${r['rate_per_kwh']:.3f}/kWh, n={r['session_count']}")
    for d in [date(2019, 1, 1), date(2019, 2, 1), date(2020, 9, 1), date(2020, 11, 1),
              date(2022, 8, 1), date(2023, 1, 1), date(2026, 9, 28)]:
        print(d, "->", supercharger_rate_for_date(d, real_sessions=sessions, published_history=history))
