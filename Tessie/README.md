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
| **`charger_find_tesla.py`** | Hierarchical discovery explorer and live scraper for Tesla Superchargers and Destination Chargers with state filtering, search, TOU pricing, and registry sync. | `./Tools/charger_find_tesla.py --interactive`<br>`./Tools/charger_find_tesla.py --state NSW --sc --list`<br>`./Tools/charger_find_tesla.py --inspect 19258 --save` |
| **`charger_find.py`** | Unified multi-network EV charger discovery, comparison, and sync engine (Tesla, Chargefox/NRMA, PlugShare-aggregated networks, and a consolidated master catalog) - discovers and registers public 3rd-party chargers (Chargefox, Evie, BP Pulse, JOLT, Exploren, AmpCharge, and more) listed on PlugShare, with gross-tariff pricing and hardware detail scraping. | `./Tools/charger_find.py --near Home --dc --network plugshare`<br>`./Tools/charger_find.py --plugshare 801149 --save`<br>`./Tools/charger_find.py --refresh-prices` |

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

### 3. Tesla Charger Discovery & Live Scraping
```bash
# Interactive hierarchical drill-down menu (Region ➔ Country ➔ Type ➔ State ➔ Station):
./Tools/charger_find_tesla.py --interactive

# List all Superchargers in Australia grouped by state:
./Tools/charger_find_tesla.py --country Australia --sc --list

# Filter NSW Superchargers:
./Tools/charger_find_tesla.py --country Australia --state NSW --sc --list

# Search charging infrastructure by keyword (e.g. Miranda, Parramatta, Airport):
./Tools/charger_find_tesla.py --country Australia --search "Miranda"

# Inspect cached JSON details by Location ID, or force a live re-scrape:
./Tools/charger_find_tesla.py --inspect 19258
./Tools/charger_find_tesla.py --inspect 19258 --live --save

# Inspect directly by Find Us URL:
./Tools/charger_find_tesla.py --url "https://www.tesla.com/en_AU/findus/location/supercharger/19258"
```

### 4. PlugShare 3rd-Party Charger Discovery & Live Scraping
```bash
# DC fast chargers within 15km of a known place, sorted by state then station name:
./Tools/charger_find.py --near Home --dc --radius 15 --network plugshare

# Search PlugShare by name/keyword:
./Tools/charger_find.py --query "Bunnings" --network plugshare

# Inspect a specific station by PlugShare location ID and save it into the registry:
./Tools/charger_find.py --plugshare 801149 --save

# Backfill pricing for every currently-listed station missing a rate
# (bulk search doesn't return pricing - only the single-station detail lookup does):
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

---

## 🔒 Privacy Note

All personal datasets (`*.csv`), custom coordinates (`places.json`), tariff/config data (`config.json`), and invoice PDFs are strictly excluded from Git. Only generic templates (`places.example.json`) and public charger data scraped from public directories (Tesla Find Us, PlugShare) are tracked in the repository.
