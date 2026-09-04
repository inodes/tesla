# 🗺️ Tessie Suite: Drive Analysis, Geofencing & CSV Classifier

A suite of tools for processing, classifying, and analyzing [Tessie](https://tessie.com/) telemetry exports and cross-referencing trip timelines with TeslaCam dashcam footage.

---

## 🛠️ Tools Overview

| Tool | Purpose | Primary Commands |
| :--- | :--- | :--- |
| **`tessie_analyzer.py`** | High-level drives summary, interactive period selector, location geofencing, and TeslaCam video linking. | `./Tools/tessie_analyzer.py --drives`<br>`./Tools/tessie_analyzer.py --today`<br>`./Tools/tessie_analyzer.py --place "School"` |
| **`tessie_renamer.py`** | Inspects, categorizes, and standardizes all raw Tessie CSV files (drives, telemetry traces, charges, idles, battery, tires, alerts). | `./Tools/tessie_renamer.py --dry-run`<br>`./Tools/tessie_renamer.py --copy-to /path/to/dir`<br>`./Tools/tessie_renamer.py --in-place` |

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

# Copy and standardize all files into /Volumes/TESLADRIVE 1/Tessie/
./Tools/tessie_renamer.py --copy-to "/Volumes/TESLADRIVE 1/Tessie"
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

# Filter trips by location nickname
./Tools/tessie_analyzer.py --place "School"
```

---

## 🔒 Privacy Note
All personal datasets (`*.csv`) and custom coordinates (`places.json`) are strictly excluded from Git. Only generic templates (`places.example.json`) are tracked in the public repository.
