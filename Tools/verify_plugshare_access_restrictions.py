#!/usr/bin/env python3
"""
verify_plugshare_access_restrictions.py

BUG-089 follow-up (AGENTS.local.md): plugshare_to_registry_record() used to
only read a PlugShare location's coarse `access` code (1=Public,
2=Customer/Patron, else=Restricted), missing a separate
`access_restrictions`/`access_restriction_descriptions` list that PlugShare's
own site renders as an ADDITIONAL badge (e.g. "RESTRICTED" + "EMPLOYEES
ONLY") layered on top of that base code. That's now fixed for any station
re-scraped going forward - but the 2056-station cached registry
(Tessie/plugshare_chargers.json) only ever stored the derived label, not the
raw access_restrictions data, so the fix can't retroactively re-derive
labels for already-cached stations without a live re-check against
PlugShare's own API, one station at a time.

This script is that live re-check, scoped to exactly the stations that could
be affected: the 481 (at last count) currently labeled "Customer / Patron"
*or* "Restricted (Customers Only)" (the candidate filter matches on the
substring "customer" in the cached access_type, which both labels contain -
that's intentional: BUG-089 could have hidden a restriction under either
one), since that's the specific base code the reported station (Mortlake
Slipway) had. Public-labeled stations are left alone - genuinely nothing to
hide there.

BUG-095 (AGENTS.local.md, found while resuming a chunked --apply run):
re-running this script - whether via --limit N or letting an unbounded run
get killed by an external timeout partway through - always re-scanned the
candidate list FROM THE FRONT. A station that came back genuinely correct
("OK") was never mislabeled, so it was never removed from the "still says
Customer/Patron-ish" candidate set - which means every subsequent run just
re-verified the exact same leading candidates over and over and could NEVER
reach the tail of the list if it always got interrupted before finishing a
full pass. (Confirmed live: running --limit 5 twice in a row against the
real registry produced byte-identical output both times.) Fixed by adding a
small sidecar progress file (plugshare_access_verify_progress.json, next to
the PlugShare registry itself) recording every station's source_id once
it's been checked in the current "campaign" - candidates already in that
file are skipped on subsequent runs regardless of their current label, so a
run that gets interrupted and resumed (in as many chunks as it takes) is
guaranteed to eventually reach every candidate exactly once per campaign,
not just re-confirm the same head of the list forever. --reset clears the
progress file to start a fresh campaign (e.g. after enough time has passed
that re-verifying previously-OK stations is worthwhile again).

Usage:
    python3 Tools/verify_plugshare_access_restrictions.py            # dry run - reports only
    python3 Tools/verify_plugshare_access_restrictions.py --apply    # also writes corrected labels to the registry
    python3 Tools/verify_plugshare_access_restrictions.py --delay 2.0 --limit 50   # slower / partial run
    python3 Tools/verify_plugshare_access_restrictions.py --apply --reset          # start a fresh campaign

Re-checks each station live via PlugShareClient.get_location_details() (the
same call BUG-089 was verified against), reusing the already-fixed
plugshare_to_registry_record() conversion - no new parsing logic here, just
the iteration driver. Paced with a delay between requests (default 1.5s) to
be polite to PlugShare's API; safe to Ctrl+C (or a killed/timed-out chunk)
and re-run later - each run skips whatever this campaign has already
checked (see BUG-095 above) and picks up wherever it left off. The campaign
is considered complete once a run reports 0 remaining candidates; --reset
starts a new one from scratch.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from providers import PlugShareProvider  # noqa: E402
from providers.chargers_base import atomic_write_json  # noqa: E402

try:
    import json as _json
except ImportError:  # pragma: no cover - stdlib, always present
    _json = None


def _progress_path_for(provider: PlugShareProvider) -> str:
    """Co-locates the verify-campaign progress file next to wherever the
    PlugShare registry itself actually lives (registry_path already
    resolved iCloud/local/legacy fallbacks), rather than hardcoding a repo
    path that could point at the wrong disk on another machine."""
    return os.path.join(os.path.dirname(os.path.abspath(provider.registry_path)), "plugshare_access_verify_progress.json")


def _load_progress(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # A corrupt/unreadable progress file should never block the actual
        # verification work - just start this campaign over.
        return {}


def _save_progress(path: str, checked: dict) -> None:
    try:
        atomic_write_json(path, checked)
    except OSError as e:
        print(f"⚠ Could not save verify-progress file ({e}) - this run's progress won't be remembered next time.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Write corrected access labels back into the registry (default: dry-run report only)")
    ap.add_argument("--delay", type=float, default=1.5, help="Seconds to pace between live PlugShare requests (default: 1.5)")
    ap.add_argument("--limit", type=int, default=None, help="Only check the first N not-yet-checked-this-campaign candidates (default: all remaining)")
    ap.add_argument("--reset", action="store_true", help="Clear this campaign's progress file first, so every candidate is re-checked from scratch")
    args = ap.parse_args()

    provider = PlugShareProvider()
    records = provider.load_cached_registry()
    progress_path = _progress_path_for(provider)

    if args.reset and os.path.exists(progress_path):
        os.remove(progress_path)
        print(f"Cleared verify-campaign progress ({progress_path}) - starting a fresh campaign.\n")

    checked_this_campaign = _load_progress(progress_path)

    all_candidates = [
        r for r in records
        if "customer" in str((r.metadata or {}).get("access_type", "")).lower()
        and r.source_id
    ]
    remaining = [r for r in all_candidates if r.source_id not in checked_this_campaign]

    total_candidates = len(all_candidates)
    already_done = total_candidates - len(remaining)
    candidates = remaining[: args.limit] if args.limit else remaining

    print(
        f"Found {total_candidates} cached station(s) currently labeled Customer/Patron-ish "
        f"({already_done} already checked this campaign, {len(remaining)} remaining) - "
        f"checking {len(candidates)} now."
    )
    if not candidates:
        if total_candidates and already_done == total_candidates:
            print("Campaign complete - every candidate has been checked at least once. Use --reset to start a new campaign.")
        return

    mismatches = []
    errors = []
    for i, rec in enumerate(candidates, 1):
        old_label = (rec.metadata or {}).get("access_type", "")
        try:
            fresh = provider.sync_location(rec.source_id, auto_save=False)
        except Exception as e:
            errors.append((rec.name, rec.source_id, str(e)))
            print(f"  [{i}/{len(candidates)}] {rec.name!r} (id={rec.source_id}) -> ERROR: {e}")
            # Deliberately NOT marked checked - a network hiccup should be
            # retried next run, not permanently skipped for the campaign.
            time.sleep(args.delay)
            continue

        if fresh is None:
            errors.append((rec.name, rec.source_id, "no data returned"))
            print(f"  [{i}/{len(candidates)}] {rec.name!r} (id={rec.source_id}) -> ERROR: no data returned")
            time.sleep(args.delay)
            continue

        new_label = (fresh.metadata or {}).get("access_type", "")
        if new_label.strip().lower() != old_label.strip().lower():
            mismatches.append((rec.name, rec.source_id, old_label, new_label))
            print(f"  [{i}/{len(candidates)}] {rec.name!r} (id={rec.source_id}): {old_label!r} -> {new_label!r}  *** MISLABELED ***")
            if args.apply:
                provider.sync_location(rec.source_id, auto_save=True)
        else:
            print(f"  [{i}/{len(candidates)}] {rec.name!r} (id={rec.source_id}): OK ({old_label!r})")

        checked_this_campaign[rec.source_id] = datetime.now(timezone.utc).isoformat()
        # Saved after EVERY station, not just at the end of the loop - this
        # run may be one chunk of a much longer campaign and can be killed
        # by an external timeout at any moment (see BUG-095), so progress
        # already made must never depend on reaching the end of the loop.
        _save_progress(progress_path, checked_this_campaign)
        time.sleep(args.delay)

    print()
    print(f"Checked {len(candidates)} station(s) this run. Mislabeled: {len(mismatches)}. Errors: {len(errors)}.")
    # Based on what's ACTUALLY left in checked_this_campaign, not just
    # len(candidates) - a station that errored was deliberately never
    # marked checked, so counting it as "done" here would misreport a
    # campaign as complete when errored stations (typically delisted or
    # placeholder "(Coming Soon)" listings with no live PlugShare page)
    # will keep reappearing on every future run.
    still_remaining = len([r for r in candidates if r.source_id not in checked_this_campaign])
    if still_remaining > 0:
        if still_remaining == len(errors):
            print(f"{still_remaining} candidate(s) left, all currently erroring (delisted/placeholder listings) - safe to leave as-is, or re-run later in case they come back.")
        else:
            print(f"{still_remaining} candidate(s) left in this campaign - re-run the same command to continue where this left off.")
    elif total_candidates:
        print("Campaign complete - every candidate has now been checked at least once this campaign. Use --reset to start a new one.")
    if mismatches and not args.apply:
        print("Dry run only - re-run with --apply to write these corrections into the registry.")
    if errors:
        print("Stations that errored (network hiccup or since-removed listing) - not marked as checked, safe to re-run to retry them:")
        for name, sid, err in errors:
            print(f"  - {name!r} (id={sid}): {err}")


if __name__ == "__main__":
    main()
