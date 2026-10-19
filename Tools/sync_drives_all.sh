#!/usr/bin/env bash
# sync_drives_all.sh - the one command for the Drives domain: fetch Tessie
# drives via the Developer API and consolidate into drives_master.csv
# (see AGENTS.md REQ-033).
#
# Depends on Tessie/places.json already being maintained (tagged Home/Work/
# etc + boundaries) for the drives *analyzer*'s "Notable Destinations"
# column and place-name filtering to mean anything - this sync script does
# NOT touch places.json itself; that stays its own separate workflow, run
# via Tools/places_manager.py (e.g. `./places_manager.py review`) whenever new
# unnamed stop clusters show up.
#
# NOT covered yet: the other 4 confirmed-automatable Tessie endpoints
# (idles, tire_pressure - see TODO-011; battery_health and firmware_alerts are run by this script)
# have no sync script at all today; telemetry_stream stays permanently
# manual (no historical-pull API exists).
#
# This script is run manually for now (no scheduler wired up yet - see
# AGENTS.md TODO-011). Exits non-zero if any step fails.

set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat << 'EOF'
Usage: ./sync_drives_all.sh [-h|--help]

The one command for the DRIVES domain. Takes no arguments; running it
executes every step below in order against your real data. Does NOT itself
touch places.json - that stays places_manager.py's own separate review
workflow (drives-sync only needs to read an already-maintained
places.json, not manage it).

Steps run, in order:
  1. Tessie drives - fetch, then --consolidate
     (sync_api_tessie.py --drives --yes)
  2. Drive deep-dive telemetry backfill
     (sync_api_tessie.py --backfill-deepdive --drives --limit 0)
  3. Drive summary pages - drive.html in each drives/deepdive/<stamp>/ folder
     (+ clips/ when TeslaCam footage overlaps) and deepdive/index.html
     (drive_report.py; only new or changed drives are rebuilt)
  4. Battery health (vehicle-wide, not drives-specific - lives here since
     this runs regularly)
     (sync_api_tessie.py --battery-health)
  5. Firmware alerts (vehicle-wide; the Alerts tab reads them)
     (sync_api_tessie.py --firmware-alerts)
EOF
    exit 0
fi

echo "==> Tessie drives (fetch + consolidate)"
./sync_api_tessie.py --drives --yes

echo
echo "==> Drive deep-dive telemetry backfill"
./sync_api_tessie.py --backfill-deepdive --drives --limit 0

echo
echo "==> Drive summary pages (one drive.html per drive folder; picks up new footage)"
./drive_report.py

echo
echo "==> Battery health (vehicle-wide, not drives-specific - lives here since this runs regularly)"
./sync_api_tessie.py --battery-health

echo
echo "==> Firmware alerts (vehicle-wide)"
./sync_api_tessie.py --firmware-alerts

echo
echo "==> Done."
