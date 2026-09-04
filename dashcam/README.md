# 🚗 TeslaCam Multi-Drive Sync, Archive & ExportDash Suite

High-speed multi-drive sync, intelligent auto-pruning, storage auditing, and local browser video playback for Tesla dashcam and Sentry Mode recordings.

---

## 🌟 Key Features

1. **High-Speed Multi-Drive Sync:** Synchronizes Tesla USB and in-car SSDs (e.g. JOWUA) directly to your high-capacity Master Archive SSD (or Mac local folder via `--localsync`).
2. **ExFAT 2-Second Timestamp Parity:** Native `--modify-window=2` handling prevents false re-transfers across macOS and external drives.
3. **Safe Two-Tier Auto-Pruning:**
   - **In-Car Buffer (e.g. JOWUA):** Auto-prunes oldest `RecentClips` only when capacity exceeds 80% and all clips are verified on the Master Archive SSD.
   - **Glovebox OEM USB:** Preserves `SavedClips` and `SentryClips` permanently while managing loop headroom.
   - **Master Archive SSD:** Never deletes any footage.
4. **Local Browser Video Viewer (ExportDash):** Stitch and view all 4 synchronized camera angles (`front`, `back`, `left_repeater`, `right_repeater`) in a web interface powered by [ExportDash](https://github.com/nobig-deals/exportdash.cam).

---

## 🗄️ Drive Hierarchy

| Tier | Role | Device Example | Retention & Behavior |
| :--- | :--- | :--- | :--- |
| **Tier 1 (In-Car)** | Primary In-Car Dashcam Drive | 1TB Jowua Hub SSD | Active recording buffer. Pruned after 80% capacity once synced. |
| **Tier 2 (Glovebox)** | Glovebox Backup USB | 128GB OEM USB | Secondary buffer. Keeps saved/sentry events permanently. |
| **Tier 3 (Master Archive)** | Permanent Master Storage | 2TB / 4TB SSD or Mac Local Dir | Complete archive. Never auto-pruned. |

---

## 🚀 Usage

### 1. Multi-Drive Backup & Verification
```bash
# Run automatic multi-drive detection & sync
./Tools/tesla_sync.sh

# Perform dry run (preview payload without copying)
./Tools/tesla_sync.sh --dryrun

# Check storage breakdown & archive status
./Tools/tesla_sync.sh --status
```

### 2. View Footage with ExportDash
```bash
# Launch ExportDash web viewer on http://localhost:3000
./Tools/run_exportdash.sh
```

---

## 📜 Upstream Attribution
- ExportDash viewer powered by **[nobig-deals/exportdash.cam](https://github.com/nobig-deals/exportdash.cam)**.
