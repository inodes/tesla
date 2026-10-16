# 🗺️ Tessie Suite: Drive Analysis, Charging Reconciliation & Charger Discovery

A suite of tools for analyzing [Tessie](https://share.tessie.com/bGRu5q9S2kB) telemetry exports, reconciling charging costs against invoices, managing known places/geofences, and discovering public Tesla and 3rd-party chargers.

---

## ⚡ What is Tessie?

[Tessie](https://share.tessie.com/bGRu5q9S2kB) is the premier telemetry, analytics, and automation platform built for Tesla vehicles. It acts as an automated flight recorder and intelligence hub for your car:

- 📊 **Continuous Telemetry & Trip Logging:** Automatically records every drive, speed, power consumption, elevation, and parking idle without battery drain.
- 🔋 **Battery Health & Degradation Curves:** Benchmarks real-time battery capacity, usable kWh, and health degradation against thousands of fleet vehicles over time.
- ⚡ **Charging Cost Tracking:** Auto-logs charging sessions (AC & Superchargers), tracks electricity rates, and calculates lifetime fuel savings.
- ⌚ **Smartwatch & Voice Control:** Native Apple Watch app, iOS Lock Screen widgets, Siri Shortcuts, and Home Assistant / API integration.
- 📥 **Direct Data Export:** Allows exporting full high-resolution CSV records of your drives, telemetry streams, charges, tire pressure, and firmware alerts—powering the analyzers in this suite.
- 🎁 **Get Started with Tessie:**  Use the referral link for an extended free trial and discounts: **[https://share.tessie.com/bGRu5q9S2kB](https://share.tessie.com/bGRu5q9S2kB)**

---

## 🛠️ Tools Overview

| Tool | Purpose | Primary Commands |
| :--- | :--- | :--- |
| **`analyse_drives.py`** | Consolidates raw Tessie drive CSV exports, high-level drives summary with monthly breakdown, interactive period/day drill-down, place geofencing, and TeslaCam video linking. | `./Tools/analyse_drives.py --consolidate`<br>`./Tools/analyse_drives.py --drives`<br>`./Tools/analyse_drives.py --since wednesday` |
| **`places_manager.py`** | Location management, interactive POI lookup by address/GPS, frequent stop-cluster reviewer, and formatted place inspector (leaves chargers alone). | `./Tools/places_manager.py list`<br>`./Tools/places_manager.py lookup "109 Blaxland Rd, Ryde"`<br>`./Tools/places_manager.py review` |
| **`analyse_charging.py`** | Reconciles Tesla Supercharger & 3rd-party invoices against Tessie telemetry, calculates dispenser vs battery loss, verifies TOU rates, and audits charging costs. | `./Tools/analyse_charging.py --superchargers`<br>`./Tools/analyse_charging.py --inspect 1`<br>`./Tools/analyse_charging.py --consolidate` |
| **`charger_find.py`** | Unified multi-network EV charger discovery, pricing comparison, and sync engine (Tesla Superchargers/Destination, Chargefox/NRMA, Evie, BP Pulse, JOLT, Exploren, AmpCharge, and PlugShare), with TOU pricing evaluation and compound sorting. | `./Tools/charger_find.py --near Home --sort price`<br>`./Tools/charger_find.py --near Home --dc --radius 25`<br>`./Tools/charger_find.py --refresh-prices` |
| **`sync_fuel_history.py`** | Downloads, indexes, and queries 10+ years of NSW FuelCheck historical monthly price datasets into an accelerated SQLite database for EV vs ICE cost comparisons. | `./Tools/sync_fuel_all.sh`<br>`./Tools/sync_fuel_history.py --status`<br>`./Tools/sync_fuel_history.py --query --date 2024-06-15` |
| **`sync_invoices.py`** | Discovers, inspects, and standardizes names of PDF invoices (AGL statements, EV charging receipts, generic PDFs) with pure-Python extraction. | `./Tools/sync_invoices.py --scan --dry-run`<br>`./Tools/sync_invoices.py --inspect invoice.pdf`<br>`./Tools/sync_invoices.py --scan --rename` |

---

## 📋 Recognized Tessie CSV Export Types

`--consolidate` (in both `analyse_drives.py` and `analyse_charging.py`) detects and merges these export types by their column schema - just drop new exports in your Tessie directory or `~/Downloads` and run it:

| Detected Category | Schema Indicators | Consolidates Into |
| :--- | :--- | :--- |
| **Trip Summaries** | `Started At`, `Starting Location`, `Distance (km)` | `drives_master.csv` |
| **Drive Telemetry** | `Timestamp`, `Speed`, `Power` (per-drive deep-dive traces) | archived alongside the matching drive, kept separate from `drives_master.csv` |
| **Charging Sessions** | `Supercharging (kWh)` / `Energy Added` | `charges_master.csv` |

---

## 🚀 Quick Start Examples

### 1. Consolidate Raw Exports & Inspect Drive History
```bash
# Scan the configured Tessie directory / ~/Downloads and merge new exports into drives_master.csv
./Tools/analyse_drives.py --consolidate

# Interactive overview table and time period prompt
./Tools/analyse_drives.py --drives

# 24-hour vehicle & camera activity timeline for a date
./Tools/analyse_drives.py --timeline 2026-09-02
./Tools/analyse_drives.py --timeline yesterday

# Filter trips since a specific date or weekday, or by place nickname
./Tools/analyse_drives.py --since wednesday
./Tools/analyse_drives.py --place "School"
```

### 2. Charging & Supercharger Invoice Reconciliation
```bash
# High-level charging summary & network breakdown (Home AC, Superchargers, 3rd-Party Fast)
./Tools/analyse_charging.py

# Reconcile Superchargers only and inspect invoice matching
./Tools/analyse_charging.py --superchargers

# Preview renaming tax invoice PDFs, then execute
./Tools/analyse_charging.py --rename-invoices --dry-run
./Tools/analyse_charging.py --rename-invoices

# Deep-dive inspect session #142 (Macquarie Centre) or by date
./Tools/analyse_charging.py --inspect 142
./Tools/analyse_charging.py --inspect 2026-08-14

# Reconcile 3rd-party fast-charging sessions (Chargefox, Evie, BP Pulse, Jolt)
./Tools/analyse_charging.py --third-party

# List all registered Superchargers and 3rd-Party charging stations
./Tools/analyse_charging.py --list-chargers

# Consolidate all charges into charges_master.csv
./Tools/analyse_charging.py --consolidate
```

### 3. Multi-Network EV Charger Discovery & Pricing Comparison
```bash
# Discover chargers near a known place sorted by active $/kWh price (with distance tie-breaker):
./Tools/charger_find.py --near Home --sort price --radius 25

# Find high-power DC Fast Chargers (>= 25kW) across all networks near Parramatta:
./Tools/charger_find.py --near Parramatta --dc --sort power --radius 30

# Find all Tesla Superchargers in NSW or near a location:
./Tools/charger_find.py --near Home --sc --radius 50
./Tools/charger_find.py --state NSW --sc

# Search chargers across all networks by business or keyword (e.g. "Bunnings", "Macquarie"):
./Tools/charger_find.py --query "Bunnings"

# Inspect a specific station by PlugShare location ID and save it into the registry:
./Tools/charger_find.py --plugshare 801149 --save

# Discover charging stops along a driving route corridor (in progress order with detour distance, arrival SoC %, Stop Quality Score, and 1-click ABRP link):
./Tools/charger_find.py --from "Sydney" --to "Brisbane" --sc
./Tools/charger_find.py --from "Sydney" --to "Brisbane" --sc --sort score --limit 10
./Tools/charger_find.py --from Home --to "Canberra" --via "Goulburn" --dc --min-kw 150 --soc 90
./Tools/charger_find.py --from "Sydney" --to "Melbourne" --network ampcharge --sort price --corridor 10

# Backfill and refresh live pricing for all stations in radius:
./Tools/charger_find.py --near Home --dc --radius 15 --refresh-prices
```

### 5. Known Places & POI Lookup Engine
```bash
# List all stored locations in a formatted table sorted by proximity to a reference place:
./Tools/places_manager.py list --near "Home"

# Search stored places by name or address keyword:
./Tools/places_manager.py list --search "Ryde"

# Interactively look up an address or coordinates using OpenStreetMap / Overpass POI API:
./Tools/places_manager.py lookup "109 Blaxland Road, Ryde"
./Tools/places_manager.py lookup -33.81228 151.10611

# Interactively select, search, and edit saved places:
./Tools/places_manager.py edit
./Tools/places_manager.py edit "Top Ryde City Shopping Centre"

# Scan drive history for unlabelled recurring stop clusters and interactively tag POIs:
./Tools/places_manager.py review --min-stops 2

# Add, update, or alias places directly from the CLI:
./Tools/places_manager.py add "Top Ryde City Shopping Centre" --address "109 Blaxland Road, Ryde" --radius 200
./Tools/places_manager.py update "Top Ryde City Shopping Centre" --add-keyword "Devlin Street"
./Tools/places_manager.py alias "Top Ryde City Shopping Centre" --address "1 Devlin Street" --expand-radius
./Tools/places_manager.py remove "Old Place"
```

### 6. NSW FuelCheck Historical Pricing & Green Vehicle Guide (GVG) Comparison
```bash
# Sync entire 10+ year historical archive and ongoing monthly datasets:
./Tools/sync_fuel_all.sh

# Display status, database size, and indexed daily average count:
./Tools/sync_fuel_history.py --status

# Look up historical NSW petrol and diesel prices and benchmark vehicle costs for any date:
./Tools/sync_fuel_history.py --query --date 2024-06-15

# View official Australian Green Vehicle Guide (GVG) Top 20 Best-Selling Vehicles:
./Tools/analyse_charging.py --gvg-top20

# Search comparison vehicles by make, model, category, or fuel type:
./Tools/analyse_charging.py --search-vehicles "hybrid"
./Tools/analyse_charging.py --search-vehicles "diesel"

# Map EV charging costs and net savings against any GVG vehicle:
./Tools/analyse_charging.py --vehicle ford_ranger_diesel
./Tools/analyse_charging.py --vehicle rav4_hybrid

# Generate full side-by-side comparison matrix across all configured vehicles:
./Tools/analyse_charging.py --compare-all
```


---

## 🔒 Privacy Note

All personal datasets (`*.csv`), custom coordinates (`places.json`), tariff/config data (`config.json`), and invoice PDFs are strictly excluded from Git. Only generic templates (`places.example.json`) and public charger data scraped from public directories (Tesla Find Us, PlugShare) are tracked in the repository.
