# TeslaCam Multi-Drive Suite

A modular toolset for managing, backing up, analyzing, and viewing Tesla dashcam, Sentry, and continuous driving (`RecentClips`) footage across multiple storage devices on macOS and Linux.

---

## 🧩 Two Distinct Components

This repository consists of two separate, independent components:

| Component | Purpose | Dependencies |
| :--- | :--- | :--- |
| **`tesla_sync.sh`** | **Core Sync & Archive Engine**<br>• Backs up USB, Jowua, or SSDs to 2TB Master SSD or Local Directory/NAS<br>• Daily driving timeline reports & verified 100% safe retention pruning | Lightweight standalone tool.<br>Requires only standard **`rsync`** and **`python3`**.<br>*(Docker is NOT needed)* |
| **`run_exportdash.sh`** | **Optional Local Web Player & 4-Way Stitcher**<br>• Local offline container wrapper for [ExportDash](https://exportdash.cam/)<br>• Browser-based 4-camera sync playback & 2x2 grid video rendering | **Optional**.<br>Requires **OrbStack** or **Docker** only if you want to run the web UI offline on your own machine. |

### Component 1: `tesla_sync.sh` (Core Sync & Backup)
- **What it does:** Automatically discovers connected Tesla USBs, Jowua hubs, or SSDs and syncs footage to your Master Archive (either an external 2TB SSD or a local Mac folder via `--localsync`).
- **Safety Guarantee:** Verified zero-data-loss pruning engine (never deletes footage from recording drives unless confirmed present in the master archive).
- **Requirements:** Standalone script requiring only `rsync` and `python3`. **Docker is NOT required.**

### Component 2: `run_exportdash.sh` (Optional Local Web Player & Stitcher)
- **What it does:** A local Docker wrapper that runs the **ExportDash** multi-camera web player on `http://localhost:3000` and batch-renders 4-camera recordings into 2x2 grid MP4 videos (`tesla-stitch`).
- **Attribution & Upstream Project:** This component is based on the open-source project **[exportdash.cam](https://github.com/nobig-deals/exportdash.cam)** created by **[nobig-deals](https://github.com/nobig-deals)**.
- **Hosted Version Available:** You can use **[https://exportdash.cam/](https://exportdash.cam/)** directly in any web browser without running Docker or installing anything locally.
- **Docker Requirement:** A container engine (such as [OrbStack](https://orbstack.dev/) or Docker Desktop) is **strictly only required if you choose to run this local offline wrapper**.

---

## 💾 Understanding Drive Types & Volume Discovery

Tesla vehicles format and name USB recording drives as **`TESLADRIVE`** by default. When multiple Tesla drives are connected simultaneously to macOS, the operating system mounts them under `/Volumes` with sequential naming (e.g. `/Volumes/TESLADRIVE`, `/Volumes/TESLADRIVE 1`, `/Volumes/TESLADRIVE 2`).

### Storage Tiers

| Drive Type | Typical Capacity | Role & Retention Characteristics |
| :--- | :--- | :--- |
| **Factory Glovebox USB** | 128 GB / 256 GB / 512 GB | **Everyday Sentry & Saved Clips.**<br>Good for standard alerts, but Tesla purges older continuous driving clips (`RecentClips`) after ~1 hour to prevent disk full errors. |
| **High-Capacity Car SSD** | 1 TB / 2 TB (e.g. Jowua, Samsung T7) | **Extended 24h–60h Continuous Driving Buffer.**<br>Retains **24 to 60+ continuous driving hours** (~13.6 GB/hr across 4 cameras), preserving weeks of driving history to ensure you never miss unsaved damage. |
| **Master Archive Target** | 2 TB+ SSD or Local Folder / NAS | **Permanent Long-Term Archive.**<br>Master copy of all driving and Sentry events. All pruning operations strictly verify against this archive before deleting from in-car drives. |

### Run From Anywhere & Automatic Discovery
- `tesla_sync.sh` can be executed from **any directory** (e.g. your cloned repository, home folder, or from the drive itself).
- It automatically scans `/Volumes` for any drives named `TESLADRIVE*` or tagged with identity markers (`ARCHIVE_2TB`, `JOWUA_1TB`, `TESLA_USB_128GB`), categorizes their storage tier, and executes synchronization and verified pruning.

### Install & Update Tools Across Drives
To copy or update the latest version of `tesla_sync.sh` into `<Drive>/Tools` on all connected Tesla drives:

```bash
./tesla_sync.sh --install-tools
```

### Optional Overrides (`--source` and `--localsync`)
If your drives have custom volume names (not named `TESLADRIVE`), or if you prefer to sync footage to an internal Mac folder or network share:
- **`--source <PATH>`** *(Optional)* — Manually points to any drive or directory containing a `TeslaCam` folder.
- **`--localsync <DIR>`** *(Optional)* — Sets a local Mac directory or NAS folder as the master archive destination instead of an external 2TB SSD.
- **Interactive Fallback:** If standard `TESLADRIVE` volumes are not detected when running interactively in Terminal, the script will prompt you directly for the source path or local archive folder.

---

## 📋 System Prerequisites & Installation

### 1. Package Manager: Homebrew (macOS)
If you don't already have [Homebrew](https://brew.sh/) installed, run:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Core Dependencies (Required for `tesla_sync.sh`)

Install the required utilities via Homebrew:

```bash
brew install rsync python
```

> **Why modern rsync?** macOS ships with an ancient BSD rsync (v2.6.9 from 2006) which lacks delta-transfer optimisations, progress stats, and timestamp resolution required for reliable multi-gigabyte TeslaCam synchronization. Homebrew provides modern **rsync 3.x+**.

### 3. Container Engine (Optional — Strictly for `run_exportdash.sh`)

If you wish to run the local **ExportDash** web player and 4-camera video stitcher on your own machine instead of using [https://exportdash.cam/](https://exportdash.cam/):

```bash
# Recommended: OrbStack (Fast & Lightweight)
brew install --cask orbstack

# Or standard Docker Desktop
brew install --cask docker
```

---

## 🩺 System Dependency Doctor

You can audit your environment at any time to verify all utilities, versions, and active container engine:

```bash
./tesla_sync.sh --check-deps
# or
./tesla_sync.sh --doctor
```

---

## 🌟 Key Features

1. **Multi-Drive Auto-Topology Detection**
   - Automatically identifies connected drives via root identity markers (`ARCHIVE_2TB`, `JOWUA_1TB`, `TESLA_USB_128GB`, `TESLA_USB_256GB`) or partition capacity heuristics.
   - Synchronizes footage across drives in parallel with real-time transfer stats.

2. **Strict Archive Verification & Zero Data Loss Guarantee**
   - No video clip is ever pruned or deleted from recording drives unless it is **strictly verified** to exist and be complete in the master archive destination first.
   - Automatically halts all deletion if the archive destination is disconnected.
   - Includes interactive **Force Purge** (`--force-purge`) requiring typing `FORCE` to override for emergency drive clearing.

3. **Continuous Driving Ring Buffer Retention (`RecentClips`)**
   - Translates ~13.6 GB/hour of multi-camera footage into clean daily driving totals.
   - Preserves 24+ hours (up to 60+ hours on 1TB SSDs) of unbroken driving history across all camera angles.

4. **Event Location & Trigger Parsing**
   - Parses `event.json` metadata to display trigger reasons (e.g. `honk`, `dashcam_tapped`, `object_detection`) alongside reverse-geocoded street names and GPS coordinates.

---

## 🛠️ CLI Usage Reference

### 🔄 Multi-Drive Synchronization & Installation

```bash
# Default: Auto-detects connected TESLADRIVE volumes & syncs to 2TB Master SSD
./tesla_sync.sh

# Install / update the latest tesla_sync.sh onto all connected drives under <Drive>/Tools/
./tesla_sync.sh --install-tools

# Alternative: Sync directly to a local folder or NAS directory
./tesla_sync.sh --localsync ~/TeslaArchive

# Explicitly specify a custom source drive path
./tesla_sync.sh --source /Volumes/MyCustomDrive --localsync ~/TeslaArchive
```

### 📊 Storage Status & Timeline

```bash
# Display structured storage breakdown with % of drive capacity & archive status
./tesla_sync.sh --status

# Status checked against a local sync archive folder
./tesla_sync.sh --status --localsync ~/TeslaArchive

# Display storage table + daily grouped timeline (YYYY-MM-DD)
./tesla_sync.sh --timeline

# Display storage table + full expanded events with street names under each day
./tesla_sync.sh --timeline-full
```

### 🧹 Purge & Pruning Controls

```bash
# Launch interactive purge wizard (select target drive and retention interactively)
./tesla_sync.sh --purge

# Purge verified RecentClips older than 2 days
./tesla_sync.sh --purge-recent 2 --target jowua

# Purge ALL verified RecentClips from Jowua (reclaims 200+ GB immediately)
./tesla_sync.sh --purge-all-recent --target jowua

# Dry-run preview of reclaimable space without deleting
./tesla_sync.sh --purge-all-recent --target jowua --dry-run
```

### 🎬 Optional: Local Web Viewer & 4-Camera Stitching

*(Requires OrbStack or Docker)*

```bash
# Launch local 4-camera web player on http://localhost:3000
./run_exportdash.sh

# Render 4-camera composite 2x2 grid video for a specific event folder
./run_exportdash.sh --cli stitch /data/input/SavedClips/2026-08-28_21-24-42
```

---

## 🙏 Credits & Acknowledgments

- **ExportDash:** The web viewer and multi-camera compositing UI is built upon the open-source project by **[nobig-deals](https://github.com/nobig-deals)**:
  - Repository: [https://github.com/nobig-deals/exportdash.cam](https://github.com/nobig-deals/exportdash.cam)
  - Hosted Web Application: [https://exportdash.cam/](https://exportdash.cam/)
- **TeslaCam Suite:** Backup orchestration, multi-drive sync topology, daily driving timeline aggregation, and verified retention pruning by [inodes](https://github.com/inodes).

---

## 📁 Repository Structure

- [`tesla_sync.sh`](tesla_sync.sh) — Core standalone backup engine, timeline analyzer, dependency doctor, and verified purge manager.
- [`run_exportdash.sh`](run_exportdash.sh) — Optional local Docker/OrbStack runner for ExportDash web player & video stitcher.
- [`exportdash.cam/`](exportdash.cam/) — ExportDash web UI and batch 2x2 video compositor (credit: [nobig-deals/exportdash.cam](https://github.com/nobig-deals/exportdash.cam)).
