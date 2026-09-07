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
| **`tessie_drives_analyzer.py`** | Consolidates raw Tessie drive CSV exports, high-level drives summary with monthly breakdown, interactive period/day drill-down, place geofencing, and TeslaCam video linking. | `./Tools/tessie_drives_analyzer.py --consolidate`<br>`./Tools/tessie_drives_analyzer.py --drives`<br>`./Tools/tessie_drives_analyzer.py --since wednesday` |
| **`tessie_places.py`** | Location management, interactive POI lookup by address/GPS, frequent stop-cluster reviewer, and formatted place inspector (leaves chargers alone). | `./Tools/tessie_places.py list`<br>`./Tools/tessie_places.py lookup "109 Blaxland Rd, Ryde"`<br>`./Tools/tessie_places.py review` |
| **`tessie_charging_analyzer.py`** | Reconciles Tesla Supercharger & 3rd-party invoices against Tessie telemetry, calculates dispenser vs battery loss, verifies TOU rates, and audits charging costs. | `./Tools/tessie_charging_analyzer.py --superchargers`<br>`./Tools/tessie_charging_analyzer.py --inspect 1`<br>`./Tools/tessie_charging_analyzer.py --consolidate` |
| **`find_tesla_chargers.py`** | Hierarchical discovery explorer and live scraper for Tesla Superchargers and Destination Chargers with state filtering, search, TOU pricing, and registry sync. | `./Tools/find_tesla_chargers.py --interactive`<br>`./Tools/find_tesla_chargers.py --state NSW --sc --list`<br>`./Tools/find_tesla_chargers.py --inspect 19258 --save` |
| **`find_plugshare_chargers.py`** | Discovers and registers public 3rd-party chargers (Chargefox, Evie, BP Pulse, JOLT, Exploren, AmpCharge, and more) listed on PlugShare, with gross-tariff pricing and hardware detail scraping. | `./Tools/find_plugshare_chargers.py --near Home --dc`<br>`./Tools/find_plugshare_chargers.py --plugshare 801149 --save`<br>`./Tools/find_plugshare_chargers.py --refresh-prices` |

---

## 📋 Recognized Tessie CSV Export Types

`--consolidate` (in both `tessie_drives_analyzer.py` and `tessie_charging_analyzer.py`) detects and merges these export types by their column schema - just drop new exports in your Tessie directory or `~/Downloads` and run it:

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
./Tools/tessie_drives_analyzer.py --consolidate

# Interactive overview table and time period prompt
./Tools/tessie_drives_analyzer.py --drives

# 24-hour vehicle & camera activity timeline for a date
./Tools/tessie_drives_analyzer.py --timeline 2026-09-02
./Tools/tessie_drives_analyzer.py --timeline yesterday

# Filter trips since a specific date or weekday, or by place nickname
./Tools/tessie_drives_analyzer.py --since wednesday
./Tools/tessie_drives_analyzer.py --place "School"
```

### 2. Charging & Supercharger Invoice Reconciliation
```bash
# High-level charging summary & network breakdown (Home AC, Superchargers, 3rd-Party Fast)
./Tools/tessie_charging_analyzer.py

# Reconcile Superchargers only and inspect invoice matching
./Tools/tessie_charging_analyzer.py --superchargers

# Preview renaming tax invoice PDFs, then execute
./Tools/tessie_charging_analyzer.py --rename-invoices --dry-run
./Tools/tessie_charging_analyzer.py --rename-invoices

# Deep-dive inspect session #142 (Macquarie Centre) or by date
./Tools/tessie_charging_analyzer.py --inspect 142
./Tools/tessie_charging_analyzer.py --inspect 2026-08-14

# Reconcile 3rd-party fast-charging sessions (Chargefox, Evie, BP Pulse, Jolt)
./Tools/tessie_charging_analyzer.py --third-party

# List all registered Superchargers and 3rd-Party charging stations
./Tools/tessie_charging_analyzer.py --list-chargers

# Consolidate all charges into charges_master.csv
./Tools/tessie_charging_analyzer.py --consolidate
```

### 3. Tesla Charger Discovery & Live Scraping
```bash
# Interactive hierarchical drill-down menu (Region ➔ Country ➔ Type ➔ State ➔ Station):
./Tools/find_tesla_chargers.py --interactive

# List all Superchargers in Australia grouped by state:
./Tools/find_tesla_chargers.py --country Australia --sc --list

# Filter NSW Superchargers:
./Tools/find_tesla_chargers.py --country Australia --state NSW --sc --list

# Search charging infrastructure by keyword (e.g. Miranda, Parramatta, Airport):
./Tools/find_tesla_chargers.py --country Australia --search "Miranda"

# Inspect cached JSON details by Location ID, or force a live re-scrape:
./Tools/find_tesla_chargers.py --inspect 19258
./Tools/find_tesla_chargers.py --inspect 19258 --live --save

# Inspect directly by Find Us URL:
./Tools/find_tesla_chargers.py --url "https://www.tesla.com/en_AU/findus/location/supercharger/19258"
```

### 4. PlugShare 3rd-Party Charger Discovery & Live Scraping
```bash
# DC fast chargers within 15km of a known place, sorted by state then station name:
./Tools/find_plugshare_chargers.py --near Home --dc --radius 15

# Search PlugShare by name/keyword:
./Tools/find_plugshare_chargers.py --search "Bunnings"

# Inspect a specific station by PlugShare location ID and save it into the registry:
./Tools/find_plugshare_chargers.py --plugshare 801149 --save

# Backfill pricing for every currently-listed station missing a rate
# (bulk search doesn't return pricing - only the single-station detail lookup does):
./Tools/find_plugshare_chargers.py --near Home --dc --radius 15 --refresh-prices
```

### 5. Known Places & POI Lookup Engine
```bash
# List all stored locations in a formatted table sorted by proximity to a reference place:
./Tools/tessie_places.py list --near "Home"

# Search stored places by name or address keyword:
./Tools/tessie_places.py list --search "Ryde"

# Interactively look up an address or coordinates using OpenStreetMap / Overpass POI API:
./Tools/tessie_places.py lookup "109 Blaxland Road, Ryde"
./Tools/tessie_places.py lookup -33.81228 151.10611

# Interactively select, search, and edit saved places:
./Tools/tessie_places.py edit
./Tools/tessie_places.py edit "Top Ryde City Shopping Centre"

# Scan drive history for unlabelled recurring stop clusters and interactively tag POIs:
./Tools/tessie_places.py review --min-stops 2

# Add, update, or alias places directly from the CLI:
./Tools/tessie_places.py add "Top Ryde City Shopping Centre" --address "109 Blaxland Road, Ryde" --radius 200
./Tools/tessie_places.py update "Top Ryde City Shopping Centre" --add-keyword "Devlin Street"
./Tools/tessie_places.py alias "Top Ryde City Shopping Centre" --address "1 Devlin Street" --expand-radius
./Tools/tessie_places.py remove "Old Place"
```

---

## 🔒 Privacy Note

All personal datasets (`*.csv`), custom coordinates (`places.json`), tariff/config data (`config.json`), and invoice PDFs are strictly excluded from Git. Only generic templates (`places.example.json`) and public charger data scraped from public directories (Tesla Find Us, PlugShare) are tracked in the repository.
