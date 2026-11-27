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
# Tesla: --sync-tesla reads tesla.com's station list and compares it with the registry; only stations
# that are new, renamed or moved are fetched (a page each), the rest stay as they are. Nothing in the
# registry is deleted. charging_stations.json is rebuilt when a Tesla registry changed.
# PlugShare: --add-all only ever adds stations not already matched in the
# registry (see the `if not ex_k:` check at its batch-add site), so this is
# safe to re-run repeatedly without duplicating existing entries.
#
# This does NOT re-check entries by age - that's a deliberate choice to keep it fast.
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
Usage: ./sync_charger_all.sh [--only NETWORK | --site TESLA_SITE] [-h|--help]

The one command for the CHARGERS domain (charging hardware/station
registries - see AGENTS.md REQ-033 for the charger/charging/charges split).
With no argument it executes every step below in order against your real
data. --only NETWORK runs just that network's step, then the export (step 12):
NETWORK is one of tesla-sc, tesla-dc, chargefox, evx, evie, bppulse, plugshare,
ampcharge, exploren, jolt, nrma. The app's Sync chargers card uses this when a
network is picked in its dropdown. Run the two scripts directly with a wider --state/--country
scope yourself if you want a broader sweep than "near Home, 50km".
Tesla steps fetch the sites that are new, renamed or moved, and every site not fetched in the
last N days (chargers.tesla_recheck_days in config.json, default 1, the app's setting too; the
environment variable CHARGER_RECHECK_DAYS overrides it for one run; 0 = every site): tesla.com's list holds
only names and positions, so a change inside a site (stalls, power, hours, prices) is only found
by fetching its page again. An empty list from tesla.com ends the step with an error.
--site NAME fetches just that Tesla site (its tesla.com name, registry name or id) whatever its age, then the export.
Several sites are fetched at once (chargers.tesla_workers in config.json, default 2, the app's setting
too; 1 = one at a time).

Steps run, in order:
  1. Tesla Superchargers      (charger_find.py --sync-tesla --sc --recheck-days N)
  2. Tesla Destination Chargers (charger_find.py --sync-tesla --dc --recheck-days N)
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

ONLY=""
SITE=""
if [[ "${1:-}" == "--site" ]]; then       # one Tesla site (name or id): fetched now, then the export
    SITE="${2:-}"
    [[ -n "$SITE" ]] || { echo "sync_charger_all.sh: --site needs a Tesla site name or id" >&2; exit 2; }
    ONLY="site"
fi
if [[ "${1:-}" == "--only" ]]; then
    ONLY="${2:-}"
    case "$ONLY" in
        tesla-sc|tesla-dc|chargefox|evx|evie|bppulse|plugshare|ampcharge|exploren|jolt|nrma) ;;
        *) echo "sync_charger_all.sh: --only needs one of tesla-sc tesla-dc chargefox evx evie bppulse plugshare ampcharge exploren jolt nrma (got '$ONLY')" >&2; exit 2 ;;
    esac
fi
RECHECK=(--recheck-days)                # Tesla: sites not fetched in the last N days are fetched again, to see what changed inside them; N is chargers.tesla_recheck_days in config.json (the app's setting too)
[[ -n "${CHARGER_RECHECK_DAYS:-}" ]] && RECHECK+=("$CHARGER_RECHECK_DAYS")
want() { [[ -z "$ONLY" || "$ONLY" == "$1" ]]; }     # no --only: every step

if [[ -n "$SITE" ]]; then
    echo "==> Tesla site: $SITE"
    ./charger_find.py --sync-tesla --site "$SITE" || exit $?
fi

if want tesla-sc; then
    echo "==> Tesla Superchargers"
    # Tesla goes through charger_find.py --sync-tesla like every other network
    # (REQ-095 tracks moving the scraper it calls out of archive/ into
    # providers/chargers/chargers_tesla.py, so nothing needs archive/ at all).
    ./charger_find.py --sync-tesla --sc "${RECHECK[@]}"
fi

if want tesla-dc; then
    echo
    echo "==> Tesla Destination Chargers"
    ./charger_find.py --sync-tesla --dc "${RECHECK[@]}"
fi

if want chargefox; then
    echo
    echo "==> Chargefox network (Australia)"
    ./charger_find.py --sync-chargefox
fi

if want evx; then
    echo
    echo "==> EVX Kerbside Polechargers (Australia)"
    ./charger_find.py --sync-evx
fi

if want evie; then
    echo
    echo "==> Evie Networks (Australia)"
    ./charger_find.py --sync-evie
fi

if want bppulse; then
    echo
    echo "==> bp pulse DC Fast Chargers (Australia)"
    ./charger_find.py --sync-bppulse
fi

if want plugshare; then
    echo
    echo "==> PlugShare 3rd-party chargers (near Home, 50km)"
    ./charger_find.py --sync-plugshare --near Home --radius 50 --add-all
fi

if want ampcharge; then
    echo
    echo "==> Ampol AmpCharge & Ampol Foodary (Australia)"
    ./charger_find.py --sync-ampcharge
fi

if want exploren; then
    echo
    echo "==> Exploren AC/DC network (Australia)"
    ./charger_find.py --sync-exploren
fi

if want jolt; then
    echo
    echo "==> Jolt DC Fast network (Australia)"
    ./charger_find.py --sync-jolt
fi

if want nrma; then
    echo
    echo "==> NRMA EV Charging network (Australia)"
    ./charger_find.py --sync-nrma
fi

echo
echo "==> Multi-View Consolidated Registry Export"
./charger_find.py --export-consolidated

echo
echo "==> Done."
