#!/usr/bin/env bash
# sync_fuel_all.sh - The one command for the Fuel domain: discover and sync
# historical and ongoing NSW FuelCheck datasets from Data.NSW CKAN (TODO-030).

set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat << 'HELP_EOF'
Usage: ./sync_fuel_all.sh [-h|--help]

The one command for the NSW FUELCHECK historical & live pricing domain.
Discovers new monthly packages from Data.NSW CKAN, downloads raw archives,
updates the SQLite database, and computes daily/monthly averages.

Steps run, in order:
  1. Synchronize all missing historical months
     (python3 sync_fuel_history.py --sync-all)
  2. Display updated database coverage and recent pricing
     (python3 sync_fuel_history.py --status)
HELP_EOF
    exit 0
fi

echo "==> Synchronizing NSW FuelCheck Historical Datasets (Data.NSW CKAN)..."
python3 sync_fuel_history.py --sync-all

echo
echo "==> Done."
