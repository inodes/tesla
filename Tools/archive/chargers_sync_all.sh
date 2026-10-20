#!/usr/bin/env bash
# chargers_sync_all.sh - the one command for the Chargers domain: pull fresh
# charger data for both registries this repo tracks - Tesla Superchargers /
# Destination Chargers via find_tesla_chargers.py, and PlugShare-listed
# 3rd-party chargers via find_plugshare_chargers.py (see AGENTS.md REQ-033).
#
# Scoped to near Home (50km radius) rather than a state/nationwide pull, to
# keep this fast and avoid PlugShare's own rate-limiting on large bursts
# (see AGENTS.md BUG-008). Run the two scripts directly with a wider
# --state/--country scope yourself if you want a broader sweep.
#
# Tesla: --all always persists (update_registry() runs unconditionally in
# scrape_all_stations() regardless of --save), and --new restricts the
# batch to stations not already in superchargers.json/destination_
# chargers.json - it uses Playwright to actually load each new station's
# page, so this can take a while for many new stations; find_tesla_
# chargers.py's own pacing delay/retry backoff handles that.
# PlugShare: --add-all only ever adds stations not already matched in the
# registry (see the `if not ex_k:` check at its batch-add site), so this is
# safe to re-run repeatedly without duplicating existing entries.
#
# This does NOT check for or refresh STALE existing entries (verification
# age) - that's a deliberate choice to keep this "fetch new" script fast.
# Run `find_tesla_chargers.py --stale --all` yourself if you want to refresh
# aging Tesla entries; PlugShare has no separate staleness flag today.
#
# This script is run manually for now. Exits non-zero if any step fails.

set -euo pipefail
cd "$(dirname "$0")"

echo "==> Tesla Superchargers (near Home, 50km)"
./find_tesla_chargers.py --sc --near Home --radius-km 50 --new --all

echo
echo "==> Tesla Destination Chargers (near Home, 50km)"
./find_tesla_chargers.py --dc --near Home --radius-km 50 --new --all

echo
echo "==> PlugShare 3rd-party chargers (near Home, 50km)"
./find_plugshare_chargers.py --near Home --radius 50 --all-types --add-all

echo
echo "==> Done."
