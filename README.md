# TeslaCam Multi-Drive Suite

An automated toolset for managing Tesla dashcam, Sentry, and continuous driving (`RecentClips`) footage across multiple storage drives on macOS and Linux.

---

## 💾 Understanding Drive Types & Volume Discovery

Tesla vehicles format and name USB recording drives as **`TESLADRIVE`** by default. When multiple Tesla drives are connected simultaneously to macOS, the operating system mounts them under `/Volumes` with sequential naming (e.g. `/Volumes/TESLADRIVE`, `/Volumes/TESLADRIVE 1`, `/Volumes/TESLADRIVE 2`).

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                             STORAGE TIERS                                   │
├───────────────────────┬────────────────────────────┬────────────────────────┤
│ Drive Type            │ Typical Capacity           │ Role & Purpose         │
├───────────────────────┼────────────────────────────┼────────────────────────┤
│ Factory Glovebox USB  │ 128 GB / 256 GB / 512 GB   │ Everyday Sentry/Honks  │
│ High-Capacity Car SSD │ 1 TB / 2 TB (e.g. Jowua)   │ 24h–60h Driving Buffer │
│ Master Archive Target │ 2 TB+ SSD or Local Folder  │ Permanent Long-Term    │
└───────────────────────┴────────────────────────────┴────────────────────────┘
```

### Run From Anywhere & Automatic Discovery
- `tesla_sync.sh` can be executed from **any location** (e.g. your cloned git repository, home directory, or from the drive itself).
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

The suite requires modern command-line utilities for high-performance rsync transfers and JSON metadata extraction.

### 1. Package Manager: Homebrew (macOS)
If you don't already have [Homebrew](https://brew.sh/) installed, run:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Core Dependencies (Required)

Install the required tools via Homebrew:

```bash
brew install rsync python
```

> **Why modern rsync?** macOS ships with an ancient BSD rsync (v2.6.9 from 2006) which lacks delta-transfer optimisations, progress stats, and timestamp resolution required for reliable multi-gigabyte TeslaCam synchronization. Homebrew provides modern **rsync 3.x+**.

### 3. Container Engine (Recommended: OrbStack)

For running the browser-based 4-camera web player and 4-way 2x2 grid batch video stitcher, [OrbStack](https://orbstack.dev/) is the recommended fast, lightweight container engine:

```bash
# Install OrbStack (Fast & Lightweight)
brew install --cask orbstack

# Or standard Docker Desktop
brew install --cask docker

# Optional standalone FFmpeg
brew install ffmpeg
```

---

## 🩺 System Dependency Doctor

You can audit your environment at any time to verify all utilities, versions, and active container engine:

```bash
./tesla_sync.sh --check-deps
# or
./tesla_sync.sh --doctor
```

```text
==========================================================================
                 TeslaCam Suite System Dependency Audit                   
==========================================================================
  ✔ python3  : v3.14.3 (/usr/bin/python3)
  ✔ rsync    : v3.5.0 (/usr/local/bin/rsync) [Modern High-Performance]
  ✔ brew     : Homebrew 6.0.19 (/usr/local/bin/brew)
  ✔ container: Docker v29.4.0 (Powered by OrbStack) [For ExportDash & Stitcher]
==========================================================================
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

5. **Multi-Camera Web Viewer & Batch Stitcher (`ExportDash`)**
   - Zero-to-standup container providing browser-based 4-way synchronized playback (`http://localhost:3000`).
   - Batch CLI stitching utility (`tesla-stitch`) to render 4-camera recordings into synchronized 2x2 grid MP4 videos.

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

### 🎬 Web Viewer & 4-Camera Stitching

```bash
# Launch multi-camera web player on http://localhost:3000
./run_exportdash.sh

# Render 4-camera composite 2x2 grid video for a specific event folder
./run_exportdash.sh --cli stitch /data/input/SavedClips/2026-08-28_21-24-42
```

---

## 📁 Repository Structure

- [`tesla_sync.sh`](tesla_sync.sh) — Multi-drive sync, local sync directory support, daily timeline analyzer, dependency doctor, and verified purge engine.
- [`run_exportdash.sh`](run_exportdash.sh) — Zero-to-standup Docker/OrbStack runner for ExportDash web player & video stitcher.
- [`exportdash.cam/`](exportdash.cam/) — Next.js + Tailwind + FFmpeg web UI and batch 2x2 video compositor.
