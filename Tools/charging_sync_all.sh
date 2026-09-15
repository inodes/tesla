#!/usr/bin/env bash
# charging_sync_all.sh - the one command for the Charging Sessions domain:
# pulls every source this repo currently knows how to automate for
# reconciling what a charging session actually cost, in order, so nothing
# gets missed by accident (see AGENTS.md REQ-030/033).
#
# Today that's:
#   1. Tessie charges -> fetch, then --consolidate into charges_master.csv
#   2. Charge deep-dive telemetry backfill -> any Supercharger/3rd-party DC
#      session missing a local charge_deepdive_*.csv gets one fetched from
#      Tessie's own /states API (tessie_api_sync.py --backfill-deepdive).
#      Evnex home sessions are deliberately excluded here - they get their
#      own richer deep-dive via step 3 below, not a redundant /states pull.
#   3. Evnex home-charger sessions -> charges/deepdive/*_Evnex_Home.json
#   4. Invoice PDFs -> rename to the standard scheme, then check invoice
#      cost/rate against what Tessie already wrote into charges_master.csv
#      and correct ONLY where they disagree - which sessions exist comes
#      entirely from Tessie (step 1), invoices are just the source of truth
#      for cost/rate specifically when the two disagree, not for
#      reconciliation in general (tessie_charging_analyzer.py
#      --rename-invoices / --update-master)
#   5. AGL usage export consolidation -> power_usage_sync.py picks up any
#      "*Usage*.csv" already sitting in the AGL invoices folder. AGL has no
#      history API, so getting a fresh export there in the first place is
#      still a manual step (see power_usage_sync.py --help) - if nothing new
#      is sitting there, it reports "Found 0 usage export file(s)" and
#      exits clean.
#   6. Energy-source cross-check prep (REQ-072) -> for every home Evnex
#      session, pre-populates the PVOutput/AGL/Evnex "*_sources.json" cache
#      that --inspect otherwise computes lazily on first look, so pulling up
#      a session's deep-dive charging info later is instant. Runs LAST,
#      after step 5, because the cross-check reads AGL's usage master data
#      and should see whatever step 5 just consolidated.
#      (tessie_charging_analyzer.py --precompute-sources)
#
# NOT covered yet ("potentially more" from REQ-033):
#   - AGL invoice PDFs (TODO-012: a Python port of the old aglstatement.sc
#     renamer hasn't been built)
#   - Solaredge solar-monitoring data (REQ-027 mentioned it as a future
#     source; nothing automated for it exists yet)
#
# This script is run manually for now (no scheduler wired up yet - see
# AGENTS.md TODO-011). Exits non-zero if any step fails, without masking
# which step it was.

set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat << 'EOF'
Usage: ./charging_sync_all.sh [-h|--help]

The one command for the CHARGING SESSIONS domain (reconciling what a real
charge cost - see AGENTS.md REQ-033 for the charger/charging/charges split).
Takes no arguments; running it executes every step below in order against
your real data, so nothing gets missed by accident.

Steps run, in order:
  1. Tessie charges - fetch, then --consolidate into charges_master.csv
     (tessie_api_sync.py --charges --yes)
  2. Charge deep-dive telemetry backfill - Superchargers/3rd-party DC only;
     Evnex home sessions are deliberately excluded here, they get their own
     richer deep-dive in step 3
     (tessie_api_sync.py --backfill-deepdive --charges --limit 0)
  3. Evnex home-charger sessions (evnex_api_sync.py)
  4. Invoice PDFs - rename to the standard scheme, then correct ONLY
     cost/rate fields in charges_master.csv where they disagree with what
     Tessie already wrote (Tessie is always the source of which sessions
     exist; invoices are the source of truth for cost/rate specifically)
     (tessie_charging_analyzer.py --rename-invoices --yes,
      then --update-master --yes)
  5. AGL usage export consolidation - picks up any "*Usage*.csv" already
     sitting in the AGL invoices folder; AGL has no history API, so getting
     a fresh export there is still a manual step (see power_usage_sync.py
     --help)
     (power_usage_sync.py)
  6. Energy-source cross-check prep - pre-populates the PVOutput/AGL/Evnex
     cross-check cache for every home Evnex session, so --inspect never
     waits on a live fetch later; runs last so it sees step 5's AGL data
     (tessie_charging_analyzer.py --precompute-sources)

NOT covered yet: AGL invoice PDFs (TODO-012), Solaredge solar-monitoring
data (mentioned in REQ-027, nothing automated exists yet). See this file's
own header comment for the full rationale - keep that comment and this
help text in sync if either changes.
EOF
    exit 0
fi

echo "==> Tessie charges (fetch + consolidate)"
./tessie_api_sync.py --charges --yes

echo
echo "==> Charge deep-dive telemetry backfill (Superchargers/3rd-party DC - Evnex home sessions have their own sync below)"
./tessie_api_sync.py --backfill-deepdive --charges --limit 0

echo
echo "==> Evnex home-charger sessions"
./evnex_api_sync.py

echo
echo "==> Invoice PDFs (rename, then correct cost/rate mismatches in charges_master.csv)"
./tessie_charging_analyzer.py --rename-invoices --yes
./tessie_charging_analyzer.py --update-master --yes

echo
echo "==> AGL usage export consolidation"
./power_usage_sync.py

echo
echo "==> Energy-source cross-check prep (PVOutput/AGL/Evnex)"
./tessie_charging_analyzer.py --precompute-sources

echo
echo "==> Done."
