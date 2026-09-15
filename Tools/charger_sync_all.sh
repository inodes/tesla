#!/usr/bin/env bash
# charger_sync_all.sh - the one command for the Chargers domain: pull fresh
# charger data for both registries this repo tracks - Tesla Superchargers /
# Destination Chargers via charger_find_tesla.py, and PlugShare-listed
# 3rd-party chargers via charger_find_plugshare.py (see AGENTS.md REQ-033).
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
# Run `charger_find_tesla.py --stale --all` yourself if you want to refresh
# aging Tesla entries; PlugShare has no separate staleness flag today.
#
# This script is run manually for now. Exits non-zero if any step fails.

set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat << 'EOF'
Usage: ./charger_sync_all.sh [-h|--help]

The one command for the CHARGERS domain (charging hardware/station
registries - see AGENTS.md REQ-033 for the charger/charging/charges split).
Takes no arguments; running it executes every step below in order against
your real data. Run the two scripts directly with a wider --state/--country
scope yourself if you want a broader sweep than "near Home, 50km".

Steps run, in order:
  1. Tesla Superchargers      (charger_find_tesla.py --sc --near Home --radius-km 50 --new --all)
  2. Tesla Destination Chargers (charger_find_tesla.py --dc --near Home --radius-km 50 --new --all)
  3. Chargefox & NRMA network  (charger_find.py --sync-chargefox)
  4. EVX Kerbside Polechargers (charger_find.py --sync-evx)
  5. Evie Networks             (charger_find.py --sync-evie)
  6. bp pulse DC Fast Chargers (charger_find.py --sync-bppulse)
  7. PlugShare 3rd-party       (charger_find.py --sync-plugshare --near Home --radius 50 --add-all)
  8. Multi-View Consolidated Registry Export (charger_find.py --export-consolidated)

See this file's own header comment for the full rationale behind each step
(why "near Home" scoping, PlugShare rate-limit avoidance (BUG-008), etc.) -
keep that comment and this help text in sync if either changes.
EOF
    exit 0
fi

echo "==> Tesla Superchargers (near Home, 50km)"
./charger_find_tesla.py --sc --near Home --radius-km 50 --new --all

echo
echo "==> Tesla Destination Chargers (near Home, 50km)"
./charger_find_tesla.py --dc --near Home --radius-km 50 --new --all

echo
echo "==> Chargefox & NRMA network (Australia)"
./charger_find.py --sync-chargefox

echo
echo "==> EVX Kerbside Polechargers (Australia)"
./charger_find.py --sync-evx

echo
echo "==> Evie Networks (Australia)"
./charger_find.py --sync-evie

echo
echo "==> bp pulse DC Fast Chargers (Australia)"
./charger_find.py --sync-bppulse

echo
echo "==> PlugShare 3rd-party chargers (near Home, 50km)"
./charger_find.py --sync-plugshare --near Home --radius 50 --add-all

echo
echo "==> Multi-View Consolidated Registry Export"
./charger_find.py --export-consolidated

echo
echo "==> Done."
