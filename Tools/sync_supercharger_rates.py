#!/usr/bin/env python3
"""
sync_supercharger_rates.py - REQ-162 (2026-09-29): scans this account's
own real Supercharger sessions (charges_master.csv, via
lib.supercharger_pricing) for rate regimes and proposes new/updated
dated entries for charges/supercharger_rate_history.json.

User: "I would be good to get averages from all superchargers moving
forward and if they differ a lot, recording it as a new
supercharger_rate_history.json entry."

Mirrors sync_agl_rates.py's own established pattern (report-only by
default, --apply to write, backup first via lib.archive_naming, sanity-
check the write after) but reads real charging SESSIONS instead of bill
PDFs - there's no invoice to extract a Supercharger rate from (REQ-160's
own module docstring: Supercharger AU pricing is a single network rate,
not billed per-account).

Regime detection (lib.supercharger_pricing.detect_real_rate_regimes()):
daytime-only real sessions (6am-6pm local, matching this account's own
observed real day/night price split - REQ-160) are segmented into
consecutive regimes wherever a session's rate strays by more than
DEFAULT_REGIME_TOLERANCE_PER_KWH ($0.03/kWh) from its regime's running
mean - see that function's own module for why $0.03 was picked.

Each detected regime is compared against the rate-history file's own
last entry (whichever came before it chronologically in this run):
  - within tolerance of a still-open "real_account_data" period ->
    UPDATE that period's rate_per_kwh/session_count in place (more
    sessions have arrived for the SAME regime, refining the average -
    never duplicated as a new period).
  - within tolerance of a fixed historical period (published or an
    already-closed-out real one) -> nothing to do, already correct.
  - diverges by more than the tolerance -> genuinely a NEW regime ->
    proposed as a new period to APPEND.

Usage:
    ./sync_supercharger_rates.py             # scan real sessions, report only
    ./sync_supercharger_rates.py --apply      # also write the change(s)
    ./sync_supercharger_rates.py --tolerance 0.05   # override the divergence threshold
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.tessie_api_common import resolve_tessie_data_home  # noqa: E402
from lib.archive_naming import next_archive_path  # noqa: E402
from lib.supercharger_pricing import (  # noqa: E402
    load_real_supercharger_sessions,
    daytime_only_sessions,
    detect_real_rate_regimes,
    RATE_HISTORY_FILENAME,
    DEFAULT_REGIME_TOLERANCE_PER_KWH,
)


def main():
    parser = argparse.ArgumentParser(
        description="Scan real Supercharger sessions for rate regimes and propose "
                    "new/updated dated entries for charges/supercharger_rate_history.json. "
                    "Report-only by default.",
    )
    parser.add_argument("--apply", action="store_true",
                         help="Write the proposed change(s) to the real rate-history file (backs up first)")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_REGIME_TOLERANCE_PER_KWH,
                         help=f"$/kWh divergence that counts as a genuine new regime (default {DEFAULT_REGIME_TOLERANCE_PER_KWH})")
    args = parser.parse_args()

    sessions = load_real_supercharger_sessions()
    day_sessions = daytime_only_sessions(sessions)
    print(f"{len(sessions)} real Supercharger session(s) total, {len(day_sessions)} in the daytime (6am-6pm) window.")
    if not day_sessions:
        print("No daytime real sessions yet - nothing to detect.")
        return

    regimes = detect_real_rate_regimes(day_sessions, tolerance=args.tolerance)
    print(f"\n{len(regimes)} daytime rate regime(s) detected in real session data:")
    for r in regimes:
        print(f"  from {r['effective_from']} (last session {r['last_session_at']}): "
              f"${r['rate_per_kwh']:.3f}/kWh, n={r['session_count']}")

    data_home = resolve_tessie_data_home()
    rates_path = os.path.join(data_home, RATE_HISTORY_FILENAME)
    if not os.path.isfile(rates_path):
        print(f"\n⚠ {rates_path} doesn't exist yet - not proposing anything into a file "
              f"this repo has never validated the shape of.")
        return
    with open(rates_path, "r", encoding="utf-8") as f:
        real_file = json.load(f)
    existing_periods = list(real_file.get("periods", []))  # copy - mutated below via append/in-place update

    # 2026-09-30 review (D1): every detected regime used to be compared
    # only against the file's LAST row, so once two real regimes were on
    # file each --apply appended both again (and one path crashed on
    # existing_periods[None]). Match each regime to what's on file by its
    # own start date instead.
    def _from(p):
        return p["effective_from"] if isinstance(p["effective_from"], str) else p["effective_from"].isoformat()

    to_append = []
    to_update = []
    for r in regimes:
        r_from = r["effective_from"].isoformat()
        same_start = [i for i, p in enumerate(existing_periods)
                      if p.get("source") == "real_account_data" and _from(p) == r_from]
        if same_start:
            i = same_start[0]
            p = existing_periods[i]
            if p.get("rate_per_kwh") != r["rate_per_kwh"] or p.get("session_count") != r["session_count"]:
                to_update.append((i, r))
            continue
        covering = [p for p in existing_periods if _from(p) <= r_from]
        cover = covering[-1] if covering else None
        if cover is not None and cover.get("rate_per_kwh") is not None and \
                abs(r["rate_per_kwh"] - cover["rate_per_kwh"]) <= args.tolerance:
            continue  # already represented by the period on file that covers this date
        to_append.append(r)

    if not to_append and not to_update:
        print("\nNothing new - every detected regime already matches what's on file.")
        return

    print("\nProposed change(s):")
    for r in to_append:
        print(f"  + APPEND from {r['effective_from']}: ${r['rate_per_kwh']:.3f}/kWh "
              f"(real account data, n={r['session_count']}, last session {r['last_session_at']})")
    for idx, r in to_update:
        old = existing_periods[idx]
        print(f"  ~ UPDATE existing period from {old['effective_from']}: "
              f"${old['rate_per_kwh']:.3f}/kWh (n={old.get('session_count', '?')}) -> "
              f"${r['rate_per_kwh']:.3f}/kWh (n={r['session_count']}, last session {r['last_session_at']})")

    if not args.apply:
        print("\n[dry-run] Nothing written - re-run with --apply to write these to the real rate-history file.")
        return

    backup_dir = os.path.join(os.path.dirname(rates_path), "archive")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = next_archive_path(backup_dir, os.path.basename(rates_path))
    shutil.copy2(rates_path, backup_path)
    print(f"\nBacked up existing rate-history file to {backup_path}")

    for idx, r in to_update:
        p = existing_periods[idx]
        p["rate_per_kwh"] = r["rate_per_kwh"]
        p["session_count"] = r["session_count"]
        p["note"] = (f"Real account data (this account's own daytime Supercharger sessions, "
                     f"charges_master.csv) - n={r['session_count']}, last session {r['last_session_at']}. "
                     f"Refined by sync_supercharger_rates.py (REQ-162).")

    for r in to_append:
        existing_periods.append({
            "effective_from": r["effective_from"].isoformat(),
            "rate_per_kwh": r["rate_per_kwh"],
            "source": "real_account_data",
            "session_count": r["session_count"],
            "note": (f"Real account data (this account's own daytime Supercharger sessions, "
                     f"charges_master.csv) - n={r['session_count']}, last session {r['last_session_at']}. "
                     f"Auto-detected by sync_supercharger_rates.py (REQ-162) as diverging from the "
                     f"prior recorded rate by more than ${args.tolerance:.2f}/kWh.")
        })

    existing_periods.sort(key=lambda p: p["effective_from"])
    real_file["periods"] = existing_periods

    tmp_path = rates_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(real_file, f, indent=2)
    os.replace(tmp_path, rates_path)

    with open(rates_path, "r", encoding="utf-8") as f:
        written = json.load(f)
    expected_count = len(existing_periods)
    if len(written.get("periods", [])) != expected_count:
        print("⚠ Sanity check failed: period count after write doesn't match expected total.")
    else:
        print(f"Sanity check passed: {len(written['periods'])} period(s) on disk.")
    print(f"Wrote {rates_path}")


if __name__ == "__main__":
    main()
