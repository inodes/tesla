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
be affected: the 481 (at last count) currently labeled "Customer / Patron",
since that's the specific base code the reported station (Mortlake Slipway)
had. Public/Restricted-labeled stations are left alone - Public genuinely
has no restriction list to hide, and Restricted is already the most
conservative label there is.

Usage:
    python3 Tools/verify_plugshare_access_restrictions.py            # dry run - reports only
    python3 Tools/verify_plugshare_access_restrictions.py --apply    # also writes corrected labels to the registry
    python3 Tools/verify_plugshare_access_restrictions.py --delay 2.0 --limit 50   # slower / partial run

Re-checks each station live via PlugShareClient.get_location_details() (the
same call BUG-089 was verified against), reusing the already-fixed
plugshare_to_registry_record() conversion - no new parsing logic here, just
the iteration driver. Paced with a delay between requests (default 1.5s) to
be polite to PlugShare's API; safe to Ctrl+C and re-run later (each run just
re-scans the current "Customer / Patron" set, which shrinks as mislabeled
ones get corrected).
"""
import argparse
import os
import sys
import time

_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from providers import PlugShareProvider  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Write corrected access labels back into the registry (default: dry-run report only)")
    ap.add_argument("--delay", type=float, default=1.5, help="Seconds to pace between live PlugShare requests (default: 1.5)")
    ap.add_argument("--limit", type=int, default=None, help="Only check the first N candidate stations (default: all)")
    args = ap.parse_args()

    provider = PlugShareProvider()
    records = provider.load_cached_registry()

    candidates = [
        r for r in records
        if "customer" in str((r.metadata or {}).get("access_type", "")).lower()
        and r.source_id
    ]
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Found {len(candidates)} cached station(s) currently labeled Customer/Patron to re-check live.")
    if not candidates:
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

        time.sleep(args.delay)

    print()
    print(f"Checked {len(candidates)} station(s). Mislabeled: {len(mismatches)}. Errors: {len(errors)}.")
    if mismatches and not args.apply:
        print("Dry run only - re-run with --apply to write these corrections into the registry.")
    if errors:
        print("Stations that errored (network hiccup or since-removed listing) - safe to re-run later:")
        for name, sid, err in errors:
            print(f"  - {name!r} (id={sid}): {err}")


if __name__ == "__main__":
    main()
