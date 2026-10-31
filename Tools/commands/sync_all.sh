#!/usr/bin/env bash
# sync_all.sh - convenience wrapper that runs every API-data sync domain in
# one command: Chargers, Charging Sessions, and Drives (see AGENTS.md
# REQ-033 for why those are three separate domains/scripts rather than one).
#
# This is NOT a replacement for sync_charger_all.sh / sync_charging_all.sh /
# sync_drives_all.sh - it just calls all three, in order, for the common
# case of "I just want everything up to date". Run one of them directly
# yourself if you only need that one domain, or want a wider scope than
# each one's own defaults (e.g. sync_charger_all.sh's "near Home, 50km").
#
# Deliberately does NOT include sync_dashcam.sh - that domain needs physical
# Tesla USB/JOWUA/2TB drives connected and can be interactive (tombstone /
# Home + Recent prompts), so it doesn't fit a single hands-off "run
# everything" command the other three domains do. Run it separately when
# drives are plugged in.
#
# Order: chargers first (fast, no dependencies, and sync_charging_all.sh's
# own cost reconciliation benefits from seeing freshly-synced charger
# tariff data), then charging sessions, then drives (fully independent of
# the other two, so it doesn't matter where it runs, but last keeps the
# most likely-to-be-slow step - Tessie drives fetch + deep-dive backfill -
# from blocking the other two if it's ever the one that hangs).
#
# This script is run manually for now (no scheduler wired up yet - see
# AGENTS.md TODO-011). Exits non-zero if any step fails, without masking
# which one it was - same as each domain script it calls.

set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat << 'EOF'
Usage: ./sync_all.sh [-h|--help]

Convenience wrapper for the three API-data sync domains (see AGENTS.md
REQ-033). Takes no arguments; running it executes every step below in
order against your real data. Does NOT include sync_dashcam.sh (needs
physical drives connected - run that one separately).

Steps run, in order:
  1. Chargers domain           (./sync_charger_all.sh)
  2. Charging Sessions domain  (./sync_charging_all.sh)
  3. Drives domain             (./sync_drives_all.sh)
  4. Fuel domain (NSW Fuel)    (./sync_fuel_all.sh)

Run any one of those four directly yourself for that domain alone, or
for a wider scope than its own defaults. See this file's own header
comment for the full rationale behind the step order.
EOF
    exit 0
fi

echo "======================================================"
echo "                    sync_all.sh                       "
echo "======================================================"
echo

echo "========== [1/4] Chargers domain =========="
./sync_charger_all.sh

echo
echo "========== [2/4] Charging Sessions domain =========="
./sync_charging_all.sh

echo
echo "========== [3/4] Drives domain =========="
./sync_drives_all.sh

echo
echo "========== [4/4] Fuel domain (NSW FuelCheck) =========="
./sync_fuel_all.sh

echo
echo "======================================================"
echo "All sync domains completed successfully."
echo "======================================================"
