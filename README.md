# TeslaCam Multi-Drive Suite

An automated toolset for managing Tesla dashcam, Sentry, and continuous driving (`RecentClips`) footage across multiple storage drives on macOS and Linux.

---

## 📋 System Prerequisites & Installation

The suite requires modern command-line utilities for high-performance rsync transfers and JSON metadata extraction.

### 1. Package Manager: Homebrew (macOS)
If you don't already have [Homebrew](https://brew.sh/) installed, open Terminal and run:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Core Dependencies (Required)

Install the required tools using Homebrew:

```bash
brew install rsync python
```

> **Why modern rsync?** macOS ships with an ancient BSD rsync (v2.6.9 from 2006) which lacks delta-transfer optimisations, progress stats, and timestamp resolution required for reliable multi-gigabyte TeslaCam synchronization. Homebrew provides modern **rsync 3.x+**.

### 3. Optional Dependencies (For Web Player & Video Stitcher)

To run the local browser-based 4-camera web player and 4-way 2x2 grid batch video stitcher:

```bash
# Docker Desktop or OrbStack
brew install --cask docker
# or
brew install --cask orbstack

# Standalone FFmpeg (if not using Docker)
brew install ffmpeg
```

---

## 🩺 System Dependency Doctor

You can audit your environment at any time to verify all utilities and versions:

```bash
./tesla_sync.sh --check-deps
# or
./tesla_sync.sh --doctor
```

---

## 🌟 Key Features

1. **Multi-Drive Auto-Topology Detection**
   - Automatically identifies connected drives via root identity markers (`ARCHIVE_2TB`, `JOWUA_1TB`, `TESLA_USB_128GB`) or partition capacity heuristics.
   - Synchronizes footage across drives in parallel with real-time transfer stats.

2. **Strict Archive Verification & Zero Data Loss Guarantee**
   - No video clip is ever pruned or deleted from recording drives unless it is **strictly verified** to exist and be complete on the 2TB Master Archive SSD first.
   - Automatically halts all deletion if the 2TB Archive SSD is disconnected.
   - Includes interactive **Force Purge** (`--force-purge`) requiring typing `FORCE` to override for emergency drive clearing.

3. **Continuous Driving Ring Buffer Retention (`RecentClips`)**
   - Translates ~13.6 GB/hour of multi-camera footage into clean daily driving totals.
   - Preserves 24+ hours (up to 60+ hours on 1TB SSDs) of unbroken driving history across all camera angles.

4. **Event Location & Trigger Parsing**
   - Parses `event.json` metadata to display trigger reasons (e.g. `honk`, `dashcam_tapped`, `object_detection`) alongside reverse-geocoded street names and GPS coordinates.

5. **Multi-Camera Web Viewer & Batch Stitcher (`ExportDash`)**
   - Zero-to-standup Docker container providing browser-based 4-way synchronized playback (`http://localhost:3000`).
   - Batch CLI stitching utility (`tesla-stitch`) to render 4-camera recordings into synchronized 2x2 grid MP4 videos.

---

## 🛠️ CLI Usage Reference

### 📊 Storage Status & Timeline

```bash
# Display structured storage breakdown with % of drive capacity & archive status
./tesla_sync.sh --status

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

- [`tesla_sync.sh`](tesla_sync.sh) — Multi-drive sync, daily timeline analyzer, dependency doctor, and verified purge engine.
- [`run_exportdash.sh`](run_exportdash.sh) — Zero-to-standup Docker runner for ExportDash web player & video stitcher.
- [`exportdash.cam/`](exportdash.cam/) — Next.js + Tailwind + FFmpeg web UI and batch 2x2 video compositor.
