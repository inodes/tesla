#!/usr/bin/env bash
# drives_sync_all.sh - the one command for the Drives domain: fetch Tessie
# drives via the Developer API and consolidate into drives_master.csv
# (see AGENTS.md REQ-033).
#
# Depends on Tessie/places.json already being maintained (tagged Home/Work/
# etc + boundaries) for the drives *analyzer*'s "Notable Destinations"
# column and place-name filtering to mean anything - this sync script does
# NOT touch places.json itself; that stays its own separate workflow, run
# via Tools/tessie_places.py (e.g. `./tessie_places.py review`) whenever new
# unnamed stop clusters show up.
#
# NOT covered yet: the other 4 confirmed-automatable Tessie endpoints
# (idles, battery_health, tire_pressure, firmware_alerts - see TODO-011)
# have no sync script at all today; telemetry_stream stays permanently
# manual (no historical-pull API exists).
#
# This script is run manually for now (no scheduler wired up yet - see
# AGENTS.md TODO-011). Exits non-zero if any step fails.

set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat << 'EOF'
Usage: ./drives_sync_all.sh [-h|--help]

The one command for the DRIVES domain. Takes no arguments; running it
executes every step below in order against your real data. Does NOT itself
touch places.json - that stays tessie_places.py's own separate review
workflow (drives-sync only needs to read an already-maintained
places.json, not manage it).

Steps run, in order:
  1. Tessie drives - fetch, then --consolidate
     (tessie_api_sync.py --drives --yes)
  2. Drive deep-dive telemetry backfill
     (tessie_api_sync.py --backfill-deepdive --drives --limit 0)
  3. Battery health (vehicle-wide, not drives-specific - lives here since
     this runs regularly)
     (tessie_api_sync.py --battery-health)
EOF
    exit 0
fi

echo "==> Tessie drives (fetch + consolidate)"
./tessie_api_sync.py --drives --yes

echo
echo "==> Drive deep-dive telemetry backfill"
./tessie_api_sync.py --backfill-deepdive --drives --limit 0

echo
echo "==> Battery health (vehicle-wide, not drives-specific - lives here since this runs regularly)"
./tessie_api_sync.py --battery-health

echo
echo "==> Done."
