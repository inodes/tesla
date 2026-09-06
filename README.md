# 🚗 Tesla Tools Suite

[![CI Status](https://github.com/inodes/tesla/actions/workflows/ci.yml/badge.svg)](https://github.com/inodes/tesla/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/inodes/tesla?color=blue&label=release)](https://github.com/inodes/tesla/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A modular suite of automation tools for Tesla owners on macOS:
- **Dashcam Suite (`dashcam/`):** High-speed multi-drive sync, storage auditing, safe two-tier auto-pruning, and local browser video playback.
- **Tessie Suite (`Tessie/`):** Automatic iCloud import, drive log analysis, spatial geofencing for known place nicknames, and entry/exit dashcam footage linking.

---

## 📁 Repository Structure

```text
tesla/
├── dashcam/                      # 🎥 ExportDash web player components & docs
│   ├── exportdash.cam/           # Next.js 4-camera browser player
│   └── README.md                 # Full Dashcam documentation
│
├── Tessie/                       # 🗺️ Tessie documentation & examples
│   ├── places.example.json       # Generic template for custom place nicknames
│   └── README.md                 # Full Tessie documentation
│
├── Tools/                        # 🛠️ Executable scripts for multi-drive deployment
│   ├── tesla_sync.sh             # Multi-drive sync, audit & prune engine
│   ├── tessie_analyzer.py        # Tessie drive analyzer & video linking
│   ├── tessie_charging_analyzer.py # Charging reconciliation & invoice parser
│   ├── tessie_renamer.py         # Tessie raw CSV categorization utility
│   └── run_exportdash.sh         # Local web player launcher
│
├── .github/                      # GitHub Actions CI & community standards
├── LICENSE                       # MIT License
└── README.md                     # Root overview (this file)
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
# Auto-import and standardize Tessie exports from iCloud
./Tools/tessie_analyzer.py --import-icloud

# Analyze trips and link entry/exit times to TeslaCam video clips
./Tools/tessie_analyzer.py --since 2026-09-01
```
### 3. Charging & Supercharger Invoice Reconciliation
```bash
# Reconcile Supercharger invoices against Tessie charging sessions
./Tools/tessie_charging_analyzer.py --superchargers

# Reconcile 3rd-Party Fast chargers (Chargefox, Evie, BP Pulse, Jolt)
./Tools/tessie_charging_analyzer.py --third-party

# Inspect charging efficiency loss and TOU tariff rate for a session
./Tools/tessie_charging_analyzer.py --inspect 142
```
👉 *See [Tessie/README.md](Tessie/README.md) for full documentation on charging reconciliation, invoices, and tariffs.*

### 4. Deploy Scripts to External Drives

```bash
# Automatically install/update all tools to /Volumes/*/Tools/ across all mounted drives
./Tools/tesla_sync.sh --install-tools
```

---

## 🔒 Privacy & PII Protection
Personal trip histories (`*.csv`) and custom location coordinates (`places.json`) contain sensitive Personally Identifiable Information (PII) and are **strictly excluded from Git tracking** via `.gitignore`. Only generic templates (`places.example.json`) are committed to the public repository.

---

## 🤝 Contributing
Contributions, bug reports, and pull requests are warmly welcomed! Please see [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 📄 License
Released under the [MIT License](LICENSE). Copyright © 2026 Glenn Stewart (inodes).
