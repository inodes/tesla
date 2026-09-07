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
├── dashcam/                        # 🎥 ExportDash web player components & docs
│   ├── exportdash.cam/             # Next.js 4-camera browser player
│   └── README.md                   # Full Dashcam documentation
│
├── Tessie/                         # 🗺️ Tessie documentation & examples
│   ├── places.example.json         # Generic template for custom place nicknames
│   └── README.md                   # Full Tessie documentation
│
├── Tools/                          # 🛠️ Executable scripts for multi-drive deployment
│   ├── tesla_sync.sh               # Multi-drive TeslaCam sync, audit & prune engine
│   ├── run_exportdash.sh           # Local ExportDash web player launcher
│   ├── tessie_drives_analyzer.py   # Drives explorer, footage linker, and master consolidator
│   ├── tessie_charging_analyzer.py # Charging reconciliation & invoice parser
│   ├── tessie_places.py            # Place tagging, geocoding, and stop-cluster review utility
│   ├── find_tesla_chargers.py      # Tesla Supercharger / Destination Charger explorer & live scraper
│   ├── find_plugshare_chargers.py  # PlugShare-listed 3rd-party charger explorer & registry
│   ├── evnex_explore.py            # Read-only dump of home charger (Evnex) status/session data
│   └── table_formatter.py          # Shared terminal table rendering helpers
│
├── .github/                        # GitHub Actions CI & community standards
├── LICENSE                         # MIT License
└── README.md                       # Root overview (this file)
```

---

## ⚡ Quick Start

### 1. Multi-Drive TeslaCam Backup
```bash
# Automatically detect all connected Tesla drives and sync to Master Archive SSD
./Tools/tesla_sync.sh

# Check storage breakdown and archive status
./Tools/tesla_sync.sh --status
```
👉 *See [dashcam/README.md](dashcam/README.md) for full documentation on drive tiers and pruning rules.*

### 2. Tessie Drive Log Analysis & Place Matching
```bash
# Consolidate newly-exported Tessie CSVs into drives_master.csv
./Tools/tessie_drives_analyzer.py --consolidate

# Interactive drives overview and time-period selector
./Tools/tessie_drives_analyzer.py --drives

# Filter trips since a specific date or weekday
./Tools/tessie_drives_analyzer.py --since wednesday
```

### 3. Charging & Supercharger Invoice Reconciliation
```bash
# Reconcile Supercharger invoices against Tessie charging sessions
./Tools/tessie_charging_analyzer.py --superchargers

# Reconcile 3rd-party fast-charging sessions (Chargefox, Evie, BP Pulse, Jolt)
./Tools/tessie_charging_analyzer.py --third-party

# Inspect charging efficiency loss and TOU tariff rate for a session
./Tools/tessie_charging_analyzer.py --inspect 142
```

### 4. Charger Discovery
```bash
# Tesla Superchargers/Destination Chargers near a known place
./Tools/find_tesla_chargers.py --address Home --sc --list

# PlugShare-listed 3rd-party DC fast chargers near a known place
./Tools/find_plugshare_chargers.py --near Home --dc --radius 15
```
👉 *See [Tessie/README.md](Tessie/README.md) for full documentation on drives, charging reconciliation, invoices, tariffs, places, and charger discovery.*

### 5. Home Charger (Evnex) Exploration

```bash
# Dump everything the `evnex` library can see about your Evnex home charger
# (status, energy meter, charging sessions) to a JSON file for inspection -
# read-only, never issues a charge command. Credentials come from
# Tessie/config.json or EVNEX_CLIENT_USERNAME/EVNEX_CLIENT_PASSWORD env vars
# (see Tools/evnex_common.py).
./Tools/evnex_explore.py
```

### 6. Deploy Scripts to External Drives

```bash
# Automatically install/update tesla_sync.sh to /Volumes/*/Tools/ across all mounted drives
./Tools/tesla_sync.sh --install-tools
```

---

## 🔒 Privacy & PII Protection
Personal trip histories (`*.csv`), custom location coordinates (`places.json`), tariff/config data (`config.json`), and Supercharger invoice PDFs contain sensitive Personally Identifiable Information (PII) and are **strictly excluded from Git tracking** via `.gitignore`. Only generic templates (`places.example.json`) and public data scraped from public charger directories are committed to the repository. See [`AGENTS.md`](AGENTS.md) for the full policy.

---

## 🤝 Contributing
Contributions, bug reports, and pull requests are warmly welcomed! Please see [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 📄 License
Released under the [MIT License](LICENSE). Copyright © 2026 Glenn Stewart (inodes).
