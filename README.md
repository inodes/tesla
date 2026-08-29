# TeslaCam Multi-Drive Suite

An automated toolset for managing Tesla dashcam, Sentry, and continuous driving (`RecentClips`) footage across multiple storage drives on macOS and Linux.

---

## 🌟 Key Features

1. **Multi-Drive Auto-Topology Detection**
   - Automatically identifies connected drives via root identity markers (`ARCHIVE_2TB`, `JOWUA_1TB`, `TESLA_USB_128GB`) or partition capacity heuristics.
   - Synchronizes footage across drives with rsync.

2. **Strict Archive Verification & Zero Data Loss Guarantee**
   - No video clip is ever pruned or deleted from recording drives unless it is **strictly verified** to exist and be complete on the 2TB Master Archive SSD first.
   - Blocks all automatic deletion if the 2TB Archive SSD is disconnected.
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

# Dry-run preview of reclaimable space
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

- `tesla_sync.sh` — Multi-drive sync, daily timeline analyzer, and verified purge engine.
- `run_exportdash.sh` — Zero-to-standup Docker runner for ExportDash web player & video stitcher.
- `exportdash.cam/` — Next.js + Tailwind + FFmpeg web UI and batch 2x2 video compositor.
