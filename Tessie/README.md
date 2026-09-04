# 🗺️ Tessie Suite: Drive Analysis, Geofencing & CSV Classifier

A suite of tools for processing, classifying, and analyzing [Tessie](https://share.tessie.com/bGRu5q9S2kB) telemetry exports and cross-referencing trip timelines with TeslaCam dashcam footage.

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
| **`tessie_analyzer.py`** | High-level drives summary, interactive period selector, location geofencing, and TeslaCam video linking. | `./Tools/tessie_analyzer.py --drives`<br>`./Tools/tessie_analyzer.py --today`<br>`./Tools/tessie_analyzer.py --place "School"` |
| **`tessie_charging_analyzer.py`** | Reconciles Tesla Supercharger & 3rd-party invoices against Tessie telemetry, calculates dispenser vs battery loss, verifies TOU rates, and audits charging costs. | `./Tools/tessie_charging_analyzer.py --superchargers`<br>`./Tools/tessie_charging_analyzer.py --inspect 1`<br>`./Tools/tessie_charging_analyzer.py --consolidate` |
| **`tessie_renamer.py`** | Inspects, categorizes, and standardizes all raw Tessie CSV files (drives, telemetry traces, charges, idles, battery, tires, alerts). | `./Tools/tessie_renamer.py --dry-run`<br>`./Tools/tessie_renamer.py --copy-to /path/to/dir`<br>`./Tools/tessie_renamer.py --in-place` |
| **`find_tesla_chargers.py`** | Hierarchical discovery explorer and live scraper for Tesla Superchargers and Destination Chargers with state filtering, search, and registry sync. | `./Tools/find_tesla_chargers.py`<br>`./Tools/find_tesla_chargers.py --state NSW --sc --list`<br>`./Tools/find_tesla_chargers.py --scrape 19258 --update --sync` |

---

## 📋 Recognized Tessie File Types

| Detected Category | Schema Indicators | Standardized Filename Pattern |
| :--- | :--- | :--- |
| **Trip Summaries** (Format A) | `Started At`, `Starting Location`, `Distance (km)` | `drives_summary_YYYY-MM-DD_to_YYYY-MM-DD.csv` |
| **Single Drive Trace** (Format B) | `Timestamp`, `Speed`, `Power` ($\le 2\text{ hours}$) | `drive_telemetry_YYYY-MM-DD_HH-MM.csv` |
| **Continuous Telemetry** | `Timestamp`, `Speed`, `Power` ($> 2\text{ hours}$) | `telemetry_stream_YYYY-MM-DD_to_YYYY-MM-DD.csv` |
| **Charging Sessions** | `Supercharging (kWh)` / `Energy Added` | `charges_YYYY-MM-DD_to_YYYY-MM-DD.csv` |
| **Parking & Idles** | `Duration`, `Location`, `Starting Battery` | `idles_parking_YYYY-MM-DD_to_YYYY-MM-DD.csv` |
| **Battery Health** | `Max Range (km)`, `Usable Capacity (kWh)` | `battery_health_YYYY-MM-DD_to_YYYY-MM-DD.csv` |
| **Tire Pressure** | `Tire`, `Pressure (psi)` | `tire_pressure_history.csv` |
| **Firmware Alerts** | `Customer Facing Message`, `Clear Condition` | `firmware_alerts_history.csv` |

---

## 🚀 Quick Start Examples

### 1. Review & Rename Raw Tessie Files
```bash
# Preview proposed standard names for files in iCloud folder
./Tools/tessie_renamer.py --dry-run

# Copy and standardize all files into mounted TESLADRIVE external volume
./Tools/tessie_renamer.py --copy-to "/Volumes/TESLADRIVE/Tessie"
```

### 2. Inspect Drive History & Match Places
```bash
# Interactive overview table and time period prompt
./Tools/tessie_analyzer.py --drives

# 24-hour 30-minute vehicle & camera activity timeline for a date
./Tools/tessie_analyzer.py --timeline 20260904
./Tools/tessie_analyzer.py --timeline 2026-09-02
./Tools/tessie_analyzer.py --timeline yesterday

# Filter trips since a specific date or weekday
./Tools/tessie_analyzer.py --since wednesday

### 3. Charging & Supercharger Invoice Reconciliation
```bash
# High-level charging summary & network breakdown (Home AC, Superchargers, 3rd-Party Fast)
./Tools/tessie_charging_analyzer.py

# Reconcile Superchargers only and inspect invoice matching
./Tools/tessie_charging_analyzer.py --superchargers

# Preview renaming tax invoice PDFs (Tesla_Supercharging_YYYYMMDDHHMM_<invoice_num>_<Location>.pdf)
./Tools/tessie_charging_analyzer.py --rename --dry-run

# Execute batch renaming of invoice PDFs
./Tools/tessie_charging_analyzer.py --rename

# Deep-dive inspect session #142 (Macquarie Centre) or by date
./Tools/tessie_charging_analyzer.py --inspect 142
./Tools/tessie_charging_analyzer.py --inspect 2026-08-14

# Reconcile 3rd-Party Fast chargers (Chargefox, Evie, BP Pulse, Jolt)
./Tools/tessie_charging_analyzer.py --third-party

# List all registered Superchargers and Time-of-Use tariffs
./Tools/tessie_charging_analyzer.py --list-chargers

# Consolidate all charges into charges_master.csv
./Tools/tessie_charging_analyzer.py --consolidate

# Synchronize tools and registries to all mounted TESLADRIVE volume(s)
./Tools/tessie_charging_analyzer.py --sync
```

### 4. Tesla Charger Discovery & Live Scraping
```bash
# Interactive hierarchical drill-down menu (Region ➔ Country ➔ Type ➔ State ➔ Station):
./Tools/find_tesla_chargers.py

# List all Superchargers in Australia grouped by state:
./Tools/find_tesla_chargers.py --country Australia --sc --list

# Filter NSW Superchargers:
./Tools/find_tesla_chargers.py --country Australia --state NSW --sc --list

# Search charging infrastructure by keyword (e.g. Miranda, Parramatta, Airport):
./Tools/find_tesla_chargers.py --country Australia --search "Miranda"

# Scrape live pricing and hardware specs by Location ID or Find Us URL:
./Tools/find_tesla_chargers.py --scrape 19258
./Tools/find_tesla_chargers.py --url "https://www.tesla.com/en_AU/findus/location/supercharger/19258"

# Scrape, update superchargers.json registry, and sync to external TESLADRIVE:
./Tools/find_tesla_chargers.py --scrape 19258 --update --sync
```

---

## 🔒 Privacy Note

All personal datasets (`*.csv`) and custom coordinates (`places.json`) are strictly excluded from Git. Only generic templates (`places.example.json`) are tracked in the public repository.
