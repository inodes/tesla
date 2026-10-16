#!/usr/bin/env bash
# sync_charger_all.sh - the one command for the Chargers domain: pull fresh
# charger data for both registries this repo tracks - Tesla Superchargers /
# Destination Chargers via charger_find.py --sync-tesla (the same entry point as
# every other network; its scraper still lives in archive/ - see REQ-095), and PlugShare-listed
# 3rd-party chargers via charger_find.py --sync-plugshare (see AGENTS.md REQ-033).
#
# Scoped to near Home (50km radius) rather than a state/nationwide pull, to
# keep this fast and avoid PlugShare's own rate-limiting on large bursts
# (see AGENTS.md ). Run the two scripts directly with a wider
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
# Run `charger_find.py --sync-tesla --stale` yourself if you want to refresh
# aging Tesla entries; PlugShare has no separate staleness flag today.
#
# (AGENTS.local.md): AmpCharge was already wired into charger_find.py's
# --sync-all but had never been added to THIS script's own step list - fixed
# here alongside adding the new Exploren network (REQ-086); Jolt (REQ-087)
# was added the same way from day one.
#
# REQ-088 (AGENTS.local.md): NRMA now has its own dedicated Tier 1 provider
# (KML export from NRMA's own Google My Maps embed), ranked below Chargefox
# specifically where the two cover the same physical site (Chargefox's data
# is richer there) - added as its own step alongside step 3's existing
# Chargefox sync, which still separately covers the ~28 NRMA sites also
# present in Chargefox's own GraphQL feed.
#
# This script is run manually for now. Exits non-zero if any step fails.

set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat << 'EOF'
Usage: ./sync_charger_all.sh [-h|--help]

The one command for the CHARGERS domain (charging hardware/station
registries - see AGENTS.md REQ-033 for the charger/charging/charges split).
Takes no arguments; running it executes every step below in order against
your real data. Run the two scripts directly with a wider --state/--country
scope yourself if you want a broader sweep than "near Home, 50km".

Steps run, in order:
  1. Tesla Superchargers      (charger_find.py --sync-tesla --sc --near Home --radius 50 --new)
  2. Tesla Destination Chargers (charger_find.py --sync-tesla --dc --near Home --radius 50 --new)
  3. Chargefox network         (charger_find.py --sync-chargefox)
  4. EVX Kerbside Polechargers (charger_find.py --sync-evx)
  5. Evie Networks             (charger_find.py --sync-evie)
  6. bp pulse DC Fast Chargers (charger_find.py --sync-bppulse)
  7. PlugShare 3rd-party       (charger_find.py --sync-plugshare --near Home --radius 50 --add-all)
  8. Ampol AmpCharge & Foodary (charger_find.py --sync-ampcharge)
  9. Exploren AC/DC network    (charger_find.py --sync-exploren)
  10. Jolt DC Fast network     (charger_find.py --sync-jolt)
  11. NRMA EV Charging network (charger_find.py --sync-nrma)
  12. Multi-View Consolidated Registry Export (charger_find.py --export-consolidated)

See this file's own header comment for the full rationale behind each step
(why "near Home" scoping, PlugShare rate-limit avoidance (), etc.) -
keep that comment and this help text in sync if either changes.
EOF
    exit 0
fi

echo "==> Tesla Superchargers (near Home, 50km)"
# Tesla goes through charger_find.py --sync-tesla like every other network
# (REQ-095 tracks moving the scraper it calls out of archive/ into
# providers_chargers/chargers_tesla.py, so nothing needs archive/ at all).
./charger_find.py --sync-tesla --sc --near Home --radius 50 --new

echo
echo "==> Tesla Destination Chargers (near Home, 50km)"
./charger_find.py --sync-tesla --dc --near Home --radius 50 --new

echo
echo "==> Chargefox network (Australia)"
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
echo "==> Ampol AmpCharge & Ampol Foodary (Australia)"
./charger_find.py --sync-ampcharge

echo
echo "==> Exploren AC/DC network (Australia)"
./charger_find.py --sync-exploren

echo
echo "==> Jolt DC Fast network (Australia)"
./charger_find.py --sync-jolt

echo
echo "==> NRMA EV Charging network (Australia)"
./charger_find.py --sync-nrma

echo
echo "==> Multi-View Consolidated Registry Export"
./charger_find.py --export-consolidated

echo
echo "==> Done."
