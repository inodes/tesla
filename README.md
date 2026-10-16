# 🚗 Tesla Tools Suite

[![CI Status](https://github.com/inodes/tesla/actions/workflows/ci.yml/badge.svg)](https://github.com/inodes/tesla/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/inodes/tesla?color=blue&label=release)](https://github.com/inodes/tesla/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A modular suite of automation tools for Tesla owners on macOS:
- **Dashcam Suite (`dashcam/`):** High-speed multi-drive sync, storage auditing, safe two-tier auto-pruning, and local browser video playback.
- **Tessie Suite (`Tessie/`):** Drive & charging analysis, spatial geofencing for known place nicknames, entry/exit dashcam footage linking, and public charger discovery (Tesla Superchargers/Destination Chargers and PlugShare-listed 3rd-party chargers).

---

## 📁 Repository Structure

```text
tesla/
├── config.example.json             # Sanitized template - copy to config.json (gitignored) with your
│                                    #   real API keys/tokens; moved here from Tessie/ since this repo
│                                    #   is no longer just a Tessie connector (drives/AGL/PVOutput/
│                                    #   dashcam/charger-discovery all live here too)
├── dashcam/                        # 🎥 ExportDash web player components & docs
│   ├── exportdash.cam/             # Next.js 4-camera browser player
│   └── README.md                   # Full Dashcam documentation
│
├── Tessie/                         # 🗺️ Tessie documentation & examples
│   ├── places.example.json         # Generic template for custom place nicknames
│   └── README.md                   # Full Tessie documentation
│
├── Tools/                          # 🛠️ Executable scripts for multi-drive deployment
│   ├── archive/                    # Archived one-off / exploratory test scripts
│   ├── lib/                        # Shared library modules imported by the scripts below (not run
│   │                                #   directly) - sei_inspector.py, table_formatter.py, tessie_api_common.py,
│   │                                #   session_cost.py (REQ-154, per-session EV cost), charge_curves.py
│   │                                #   (REQ-154, charging-curve charts) etc.
│   ├── providers_chargers/         # Pluggable charging-network adapters (Tesla/Chargefox/EVX/Evie/
│   │                                #   BP Pulse/AmpCharge/Exploren/Jolt/NRMA/PlugShare),
│   │                                #   orchestrated by charger_find.py
│   ├── providers_power/            # Pluggable power-retailer/distributor billing providers - one
│   │                                #   BasePowerProvider subclass per retailer (currently just AGL),
│   │                                #   swappable via config.json's "power_provider" key
│   ├── analyse_battery_health.py   # Longitudinal battery capacity & degradation analyser
│   ├── analyse_car_efficiency.py   # Vehicle speed & temperature efficiency curve analyser
│   ├── analyse_charging.py         # Charging reconciliation & invoice parser
│   ├── analyse_drives.py           # Drives explorer, footage linker, and master consolidator
│   ├── charger_find.py            # Unified multi-network EV charger discovery, pricing & sync engine
│   ├── places_manager.py           # Place tagging, geocoding, and stop-cluster review utility
│   ├── power_cost_calculator.py    # Full electricity-bill reconstruction (usage + supply + solar credit + demand)
│   ├── power_demand_calculator.py  # Reproduce the active power provider's residential demand charge (AGL: Ausgrid EA025)
│   ├── sync_api_evnex.py           # Idempotent sync: new completed Evnex sessions -> charges/deepdive/*.json
│   ├── sync_api_tessie.py          # Direct Tessie REST API fetch & sync engine for drives, charges, and telemetry
│   ├── sync_fuel_history.py        # NSW FuelCheck historical & live dataset harvester and SQLite database manager
│   ├── sync_invoices.py            # Generic PDF invoice ingestion, discovery, inspection, and standardization tool
│   ├── sync_power_usage.py         # Consolidate the active power provider's usage-export CSVs into one master CSV
│   ├── sync_dashcam.sh             # Multi-drive TeslaCam sync, audit & prune engine
│   ├── dashcam_exportdash.sh       # Local ExportDash web player launcher
│   ├── sync_drives_all.sh          # One command: Tessie drives, fetch + consolidate
│   ├── sync_charging_all.sh        # One command: Tessie charges + Evnex + invoices + AGL usage + PVOutput/AGL/Evnex cross-check prep, fetch + consolidate
│   ├── sync_charger_all.sh         # One command: fetch new charger hardware across every network, near Home
│   ├── sync_fuel_all.sh            # One command: sync NSW FuelCheck historical archives & live prices
│   └── sync_all.sh                 # Runs all 4 domains above in one command (not dashcam - see its own header)
│
├── .github/                        # GitHub Actions CI & community standards
├── LICENSE                         # MIT License
└── README.md                       # Root overview (this file)
```

---

## ⚡ Quick Start

👉 *In a hurry? `./Tools/sync_all.sh` runs the Drives, Charging Sessions, and Charger Discovery one-command syncs below (2, 3, 4) back to back - it's just a thin wrapper around them, not a replacement for running one on its own. Doesn't include TeslaCam sync (1) - that needs physical drives connected.*

### 1. Multi-Drive TeslaCam Backup
```bash
# Automatically detect all connected Tesla drives and sync to Master Archive SSD
./Tools/sync_dashcam.sh

# Check storage breakdown and archive status
./Tools/sync_dashcam.sh --status
```
👉 *See [dashcam/README.md](dashcam/README.md) for full documentation on drive tiers and pruning rules.*

### 2. Tessie Drive Log Analysis & Place Matching
```bash
# Consolidate newly-exported Tessie CSVs into drives_master.csv
./Tools/analyse_drives.py --consolidate

# ...or the one-command version: fetch new drives from the Tessie API and
# consolidate in one step (requires config.json's API token/VIN)
./Tools/sync_drives_all.sh

# Interactive drives overview and time-period selector
./Tools/analyse_drives.py --drives

# Filter trips since a specific date or weekday
./Tools/analyse_drives.py --since wednesday
```

### 3. Charging & Supercharger Invoice Reconciliation
```bash
# The one command for the whole charging-sessions domain: fetch new Tessie
# charges, pull new Evnex home-charger sessions, rename invoice PDFs and
# correct any cost/rate mismatch against them (Tessie is the source for
# which sessions exist; invoices are only the source of truth for cost/rate
# specifically, when the two disagree), consolidate any new AGL usage
# export, and pre-populate the PVOutput/AGL/Evnex energy-source cross-check
# cache for every home session (so a later --inspect never waits on a live
# fetch) - see AGENTS.md REQ-033 for what's covered and what's still
# manual (AGL invoices, Solaredge)
./Tools/sync_charging_all.sh

# Reconcile Supercharger invoices against Tessie charging sessions
./Tools/analyse_charging.py --superchargers

# Reconcile 3rd-party fast-charging sessions (Chargefox, Evie, BP Pulse, Jolt)
./Tools/analyse_charging.py --third-party

# Inspect charging efficiency loss and TOU tariff rate for a session
./Tools/analyse_charging.py --inspect 142
```

### 4. Charger Discovery
```bash
# The one command to pull fresh charger data near Home (50km) for all
# registries this repo tracks - see AGENTS.md REQ-033 for why it's scoped
# to Home rather than state/nationwide (PlugShare rate-limiting, BUG-008)
./Tools/sync_charger_all.sh

# Find all Tesla Superchargers near a place (or nationwide with --state)
./Tools/charger_find.py --near Home --sc --radius 25

# Find cheapest chargers sorted by price (with secondary distance sorting)
./Tools/charger_find.py --near Home --sort price --radius 25

# Find high-power DC Fast Chargers (Tesla + 3rd-party networks >= 25kW)
./Tools/charger_find.py --near Parramatta --dc --sort power --radius 30

# Discover chargers along a highway route corridor (with live Tessie battery SoC, arrival SoC projection, and 1-click ABRP road trip links):
./Tools/charger_find.py --from "Sydney" --to "Brisbane" --sc
./Tools/charger_find.py --from "Sydney" --to "Brisbane" --sc --sort score --limit 10
./Tools/charger_find.py --from Home --to "Canberra" --via "Goulburn" --dc --min-kw 150 --soc 90
./Tools/charger_find.py --from "Sydney" --to "Melbourne" --network ampcharge --sort price --corridor 10
```
👉 *See [Tessie/README.md](Tessie/README.md) for full documentation on drives, charging reconciliation, invoices, tariffs, places, ABRP EV route planning, and charger discovery.*

### 5. Home Charger (Evnex) Sync

```bash
# Turn any newly-completed Evnex session into its own deep-dive file
# (charges/deepdive/charge_deepdive_<local-time>_NN_Evnex_Home.json,
# NN a zero-padded position starting at "00") -
# idempotent (safe to re-run; keyed on Evnex's own session id, never on
# the filename), skips sessions still actively charging.
./Tools/sync_api_evnex.py
```
👉 *This is also run as part of `./Tools/sync_charging_all.sh` (see section 3) - run it standalone only if you want just the Evnex step.*

### 6. NSW FuelCheck Historical Pricing & Green Vehicle Guide (GVG) Comparison

```bash
# The one command to sync the NSW FuelCheck monthly historical pricing datasets:
./Tools/sync_fuel_all.sh

# Check database status, indexed date range, and current reference fuel prices:
./Tools/sync_fuel_history.py --status

# Query historical fuel price averages and benchmark vehicle costs for a specific date:
./Tools/sync_fuel_history.py --query --date 2024-06-15

# View official Australian Green Vehicle Guide (GVG) Top 20 Best-Selling Vehicles:
./Tools/analyse_charging.py --gvg-top20

# Search comparison vehicles by make, model, category, or fuel type:
./Tools/analyse_charging.py --search-vehicles "hybrid"
./Tools/analyse_charging.py --search-vehicles "ranger"

# Compare EV charging costs against any specific GVG vehicle (e.g. Ford Ranger, RAV4 Hybrid):
./Tools/analyse_charging.py --vehicle ford_ranger_diesel
./Tools/analyse_charging.py --vehicle rav4_hybrid

# Generate side-by-side net savings matrix against all benchmark comparison vehicles:
./Tools/analyse_charging.py --compare-all
```
👉 *Historical pricing is indexed into `<tessie_data_home>/fuel/fuel_history.db` (SQLite) with microsecond queries and zero external dependencies. Comparison vehicles and running costs (maintenance & tyres) are sourced from the Australian Green Vehicle Guide (ADR 81/02) and fully configurable in `Tessie/comparison_vehicles.json`.*


### 7. Generic PDF Invoice Ingestion & AGL Extractor

```bash
# Preview unstandardized PDFs across default directories (~/Downloads, PDF/AGL):
./Tools/sync_invoices.py --scan --dry-run

# Deep-dive inspect any invoice PDF and extract structured metadata:
./Tools/sync_invoices.py --inspect ~/Downloads/invoice.pdf

# Rename and standardize AGL statements in-place:
./Tools/sync_invoices.py --scan "~/Library/Mobile Documents/com~apple~CloudDocs/PDF/AGL" --rename

# Move and import discovered invoices into canonical provider directories:
./Tools/sync_invoices.py --scan ~/Downloads --import
```
👉 *Supports AGL energy statements (`Statement_electricity_YYYYMMDD.pdf`), EV charging receipts (`YYYYMMDD_HHMM_Network_Inv_Place.pdf`), and generic invoices with zero external dependencies.*

### 8. Deploy Scripts to External Drives

```bash
# Automatically install/update sync_dashcam.sh to /Volumes/*/Tools/ across all mounted drives
./Tools/sync_dashcam.sh --install-tools
```

---

### 9. The Tesla App (activity, drives, charges, battery, analytics, dashcam)

A local app (Python only, reads the iCloud `Tesla` folder, nothing is changed by looking) in the style of Tessie. Type each command on its own line; do not paste a trailing `# comment` after a command, because an interactive zsh passes the `#` through as an argument.

```bash
cd path/to/tesla/Tools
./tesla_app.py
```

That opens a native window if `pywebview` is installed, otherwise your default browser (`http://127.0.0.1:8765`). Useful variants: `./tesla_app.py --browser` (always use the browser), `./tesla_app.py --no-window --port 8800` (server only), `./tesla_app.py --data-dir DIR` (another data folder), `./tesla_app.py --help`.

Native window: pywebview is now in requirements.txt, so direnv installs it into the repo venv the next time you enter the repo (or run `pip install -r requirements.txt` there). Do not install it globally.

```bash
cd path/to/tesla
pip install -r requirements.txt
```

Optional double-clickable app in `~/Applications` (a launcher around `tesla_app.py`, no Xcode; rebuild only if you move the repo; log in `~/Library/Logs/tesla_app.log`):

```bash
./build_mac_app.sh
```

Battery (charge level over time, parked drain, usual charge target) and Battery health (usable capacity and range against odometer, as `analyse_battery_health.py`) are separate pages, as in Tessie. Every listing runs newest to oldest, ordered by the UTC time (which never repeats or skips) and shown in local time. Activity, Drives, Charges, Parked and Battery have a Tessie-style date picker (presets and a calendar; days with records are bold) that selects by local day.

Charges: every charge reconciled with its invoice as in `./analyse_charging.py` (a plug-in Tessie recorded as several sessions is one charge with its records as parts; invoice details, whether Tessie holds the invoice cost, losses, telemetry, charging curves). Invoices tab lists every invoice. Reading only - to send invoice costs to Tessie use `./analyse_charging.py --sync-tessie-costs --apply`.
Places (saved places, Superchargers named as on tesla.com, a Chargers tab with every charger from `charging_stations.json` and a **Sync chargers** button that runs `./sync_charger_all.sh`, nearby OpenStreetMap places, add / edit) and Parked (with saved Sentry footage) are in the sidebar. Street-accurate drive paths: `./drive_gps.py --budget 600` (newest drives first, safe to re-run) writes a mirror of each Tessie drive CSV with the dashcam's GPS fixes added, then `./drive_report.py` redraws the maps.

Drives shorter than 50 m (and 10 min) are **re-parks**: Tessie records every shuffle of the car in a car park as a drive, so the app keeps them apart. Drives and Activity hide them by default (a Re-parks filter / chip shows them), and a place counts them separately instead of as arrivals. The time between arriving at a place and the next real drive leaving it is a parked period (a "stay") unless Tessie already has a parked or charge record for it. The limits are settings: `app.drives.repark_max_km` / `repark_max_minutes` in `config.json` (all app, map and lookup settings live in its `app` block; see `config.example.json`). Places also have a List / Map toggle, and "what is around here" says when OpenStreetMap's servers did not answer instead of showing an empty street.

Dashcam clips stream from the Archive drive (found by its `ARCHIVE_2TB` folder, never by volume name). Footage actions queued on a drive page (copy, delete, save as a clip) run from the Queue page: Preview first, then Run for real.

## 🔒 Privacy & PII Protection
Personal trip histories (`*.csv`), custom location coordinates (`places.json`), tariff/config data (`config.json`), and Supercharger invoice PDFs contain sensitive Personally Identifiable Information (PII) and are **strictly excluded from Git tracking** via `.gitignore`. Only generic templates (`places.example.json`) and public data scraped from public charger directories are committed to the repository. See [`AGENTS.md`](AGENTS.md) for the full policy.

---

## 🤝 Contributing
Contributions, bug reports, and pull requests are warmly welcomed! Please see [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 📄 License
Released under the [MIT License](LICENSE). Copyright © 2026 Glenn Stewart (inodes).
