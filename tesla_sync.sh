#!/bin/zsh

# Resolve absolute path to this running script
SCRIPT_SELF="${(%):-%N}"
if [[ -z "$SCRIPT_SELF" || ! -f "$SCRIPT_SELF" ]]; then
  SCRIPT_SELF="${BASH_SOURCE[0]:-$0}"
fi
SCRIPT_PATH="$(cd "$(dirname "$SCRIPT_SELF")" 2>/dev/null && pwd)/$(basename "$SCRIPT_SELF")"

STATE_DIR="/tmp/teslacam_sync"
PID_FILE="${STATE_DIR}/sync.pid"
LOG_FILE="${STATE_DIR}/rsync.log"

mkdir -p "$STATE_DIR"

# Resolve modern rsync binary (Homebrew priority)
RSYNC_BIN="/usr/bin/rsync"
if [[ -x "/opt/homebrew/bin/rsync" ]]; then
  RSYNC_BIN="/opt/homebrew/bin/rsync"
elif [[ -x "/usr/local/bin/rsync" ]]; then
  RSYNC_BIN="/usr/local/bin/rsync"
elif command -v rsync >/dev/null 2>&1; then
  RSYNC_BIN="$(command -v rsync)"
fi

STATUS_MODE=0
TIMELINE_MODE=0 # 0=summary table only, 1=daily, 2=full expanded
CHECK_DEPS_MODE=0
INSTALL_TOOLS_MODE=0
INSTALL_BIN_DIR=""

# Sync destination override (Local directory / NAS alternative to 2TB SSD)
LOCAL_SYNC_DIR=""
CUSTOM_SOURCE=""

# Purge controls
PURGE_MODE=""       # "wizard", "recent", "all_recent", "days", "capacity", "force"
PURGE_DAYS=""
PURGE_TARGET_PCT=""
PURGE_TARGET_DRIVE=""
DRY_RUN=0
ASSUME_YES=0
FORCE_MODE=0

# ==============================================================================
# REQUIREMENT & DEPENDENCY CHECKER
# ==============================================================================
check_system_requirements() {
  local is_explicit="${1:-0}"
  local has_issues=0
  local has_warnings=0
  local brew_path=""

  if command -v brew >/dev/null 2>&1; then
    brew_path="$(command -v brew)"
  elif [[ -x "/opt/homebrew/bin/brew" ]]; then
    brew_path="/opt/homebrew/bin/brew"
  elif [[ -x "/usr/local/bin/brew" ]]; then
    brew_path="/usr/local/bin/brew"
  fi

  if (( is_explicit == 1 )); then
    echo "=========================================================================="
    echo "                 TeslaCam Suite System Dependency Audit                   "
    echo "=========================================================================="
  fi

  # 1. Check Python 3
  local py_ver=""
  if command -v python3 >/dev/null 2>&1; then
    py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || echo "detected")"
    if (( is_explicit == 1 )); then
      echo "  ✔ python3  : v${py_ver} ($(which python3))"
    fi
  else
    echo "  ❌ python3  : Not found (Required for JSON metadata & timeline processing)"
    has_issues=1
  fi

  # 2. Check Rsync
  local rsync_ver=""
  local rsync_major=0
  if [[ -x "$RSYNC_BIN" ]]; then
    rsync_ver="$("$RSYNC_BIN" --version 2>/dev/null | head -n 1 | awk '{print $3}')"
    rsync_major="$(echo "$rsync_ver" | awk -F'.' '{print $1}')"
    if (( rsync_major >= 3 )); then
      if (( is_explicit == 1 )); then
        echo "  ✔ rsync    : v${rsync_ver} (${RSYNC_BIN}) [Modern High-Performance]"
      fi
    else
      if (( is_explicit == 1 )); then
        echo "  ⚠️ rsync    : v${rsync_ver} (${RSYNC_BIN}) [Legacy macOS 2006 BSD version]"
        echo "               (Recommended: upgrade to rsync 3.x+ for faster transfers & stats)"
      fi
      has_warnings=1
    fi
  else
    echo "  ❌ rsync    : Not found (Required for multi-drive file sync)"
    has_issues=1
  fi

  # 3. Check Homebrew
  if (( is_explicit == 1 )); then
    if [[ -n "$brew_path" ]]; then
      local brew_ver="$("$brew_path" --version 2>/dev/null | head -n 1)"
      echo "  ✔ brew     : ${brew_ver} (${brew_path})"
    else
      echo "  ℹ brew     : Not installed (https://brew.sh/)"
    fi

    # 4. Check Container Engine (OrbStack / Docker for ExportDash player/stitcher)
    local docker_bin=""
    local engine_type="Docker"
    if [[ -d "/Applications/OrbStack.app" ]] || [[ -d "$HOME/.orbstack" ]]; then
      engine_type="OrbStack"
    fi

    if command -v docker >/dev/null 2>&1; then
      docker_bin="$(command -v docker)"
    elif [[ -x "$HOME/.orbstack/bin/docker" ]]; then
      docker_bin="$HOME/.orbstack/bin/docker"
    elif [[ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]]; then
      docker_bin="/Applications/Docker.app/Contents/Resources/bin/docker"
    fi

    if [[ -n "$docker_bin" ]]; then
      local d_ver="$("$docker_bin" --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
      if [[ "$engine_type" == "OrbStack" ]]; then
        echo "  ✔ container: Docker v${d_ver} (Powered by OrbStack) [For ExportDash & Stitcher]"
      else
        echo "  ✔ container: Docker v${d_ver} (${docker_bin}) [For ExportDash & Stitcher]"
      fi
    else
      echo "  ℹ container: Optional (Install OrbStack: brew install --cask orbstack)"
    fi
    echo "=========================================================================="
  fi

  # Guide user if requirements are missing or warnings exist in doctor mode
  if [[ "$has_issues" -eq 1 ]] || [[ "$is_explicit" -eq 1 && ( "$has_warnings" -eq 1 || -z "$brew_path" ) ]]; then
    echo ""
    echo "📦 Package Installation & Upgrade Guide:"
    if [[ -z "$brew_path" ]]; then
      echo "  1. Install Homebrew (macOS Package Manager):"
      echo '     /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
      echo "     More info: https://brew.sh/"
      echo ""
    fi
    echo "  2. Install required utilities via Homebrew:"
    echo "     brew install rsync python"
    echo ""
    echo "  3. Optional (for web player & 4-camera video stitching):"
    echo "     brew install --cask orbstack"
    echo "=========================================================================="
  fi

  if (( has_issues == 1 )); then
    exit 1
  fi
}

# ==============================================================================
# SCRIPT INSTALLER & DRIVE SYNC
# ==============================================================================
install_tools_to_drives() {
  echo "=========================================================================="
  echo "                 🚗 Installing Tools to Connected Drives                  "
  echo "=========================================================================="
  echo "Source Script: $SCRIPT_PATH"
  echo ""
  local count=0

  for vol in /Volumes/*; do
    [[ -d "$vol" ]] || continue
    if [[ -d "$vol/TeslaCam" || -d "$vol/ARCHIVE_2TB" || -d "$vol/JOWUA_1TB" || -d "$vol/TESLA_USB_128GB" || -d "$vol/TESLA_USB_256GB" || -d "$vol/TESLA_USB" || "$(basename "$vol")" =~ ^TESLADRIVE ]]; then
      local tools_dir="$vol/Tools"
      mkdir -p "$tools_dir"
      cp "$SCRIPT_PATH" "$tools_dir/tesla_sync.sh"
      chmod +x "$tools_dir/tesla_sync.sh"
      echo "  ✔ Updated: $tools_dir/tesla_sync.sh"
      ((count++))
    fi
  done

  if (( count == 0 )); then
    echo "  ⚠️ No mounted Tesla volumes detected under /Volumes/."
  else
    echo ""
    echo "=========================================================================="
    echo "Successfully updated tesla_sync.sh on $count drive(s)."
    echo "=========================================================================="
  fi
  exit 0
}

install_to_bin() {
  local target_dir="${1:-/usr/local/bin}"
  target_dir="${target_dir/#\~/$HOME}"
  mkdir -p "$target_dir" 2>/dev/null || sudo mkdir -p "$target_dir"
  if [[ -w "$target_dir" ]]; then
    cp "$SCRIPT_PATH" "$target_dir/tesla_sync"
    chmod +x "$target_dir/tesla_sync"
  else
    sudo cp "$SCRIPT_PATH" "$target_dir/tesla_sync"
    sudo chmod +x "$target_dir/tesla_sync"
  fi
  echo "✔ Installed global binary: $target_dir/tesla_sync"
  echo "You can now run 'tesla_sync' from any directory in Terminal."
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-tools|--update-drives|--sync-tools)
      INSTALL_TOOLS_MODE=1
      shift
      ;;
    --install-bin|--install-global)
      INSTALL_BIN_DIR="${2:-/usr/local/bin}"
      if [[ -n "$2" && "$2" != -* ]]; then
        shift 2
      else
        shift
      fi
      install_to_bin "$INSTALL_BIN_DIR"
      ;;
    --check-deps|--doctor|--requirements)
      CHECK_DEPS_MODE=1
      shift
      ;;
    --localsync|--local-sync|--dest|--archive-dir|-ls)
      LOCAL_SYNC_DIR="$2"
      shift 2
      ;;
    --source|-src)
      CUSTOM_SOURCE="$2"
      shift 2
      ;;
    --status|-s|--summary)
      STATUS_MODE=1
      TIMELINE_MODE=0
      shift
      ;;
    --timeline-full|-tf|--full|-f|--detail|--detailed)
      STATUS_MODE=1
      TIMELINE_MODE=2
      shift
      ;;
    --timeline|-t|--days|--daily|-d)
      STATUS_MODE=1
      TIMELINE_MODE=1
      shift
      ;;
    --purge|-p)
      PURGE_MODE="wizard"
      shift
      ;;
    --force-purge|--purge-force|-pf|--force)
      PURGE_MODE="wizard"
      FORCE_MODE=1
      shift
      ;;
    --purge-recent|-pr)
      PURGE_MODE="recent"
      PURGE_DAYS=2
      if [[ -n "$2" && "$2" =~ ^[0-9]+$ ]]; then
        PURGE_DAYS="$2"
        shift
      fi
      shift
      ;;
    --purge-all-recent|--clear-recent)
      PURGE_MODE="all_recent"
      PURGE_DAYS=0
      shift
      ;;
    --purge-days|-pd)
      PURGE_MODE="days"
      PURGE_DAYS="${2:-5}"
      shift 2
      ;;
    --purge-to|-pt)
      PURGE_MODE="capacity"
      PURGE_TARGET_PCT="${2:-50}"
      shift 2
      ;;
    --target|-tgt)
      PURGE_TARGET_DRIVE="$2"
      shift 2
      ;;
    --dry-run|-n)
      DRY_RUN=1
      shift
      ;;
    --yes|-y)
      ASSUME_YES=1
      shift
      ;;
    --help|-h)
      cat << 'HELP_EOF'
Usage: tesla_sync.sh [options]

Note on Execution & Drive Discovery:
  tesla_sync.sh can be run from ANY directory (e.g. ~/iCloud/repos/tesla,
  /Volumes/TESLADRIVE 1/Tools, or installed to /usr/local/bin).
  By default, it automatically discovers connected Tesla USBs, SSDs, and Archive
  drives mounted under /Volumes.

Installation & Drive Sync:
  --install-tools            Copy/update this script into <Drive>/Tools on each connected volume
  --install-bin [DIR]        Install as a global command (default: /usr/local/bin/tesla_sync)

Status & Summary:
  -s,  --status, --summary   Show structured storage table with % of drive & archive status
  -t,  --timeline            Show storage table + daily grouped timeline (YYYY-MM-DD)
  -tf, --timeline-full       Show storage table + full expanded events under each day

Optional Source & Archive Target Overrides:
  -ls, --localsync <DIR>     Sync footage to a local folder or NAS directory (e.g. ~/TeslaArchive)
  -src,--source <PATH|DRIVE> Explicitly specify source path containing 'TeslaCam' (e.g. /Volumes/USB)

Interactive Purge:
  -p,  --purge               Launch interactive purge wizard (select drive & rate interactively)
  -pf, --force-purge         Launch interactive FORCE purge (delete without archive verification)

CLI Purge Controls (Default: Requires Archive Drive/Folder Connected):
  -pr, --purge-recent [DAYS] Purge verified RecentClips older than DAYS (default: 2 days)
       --purge-all-recent    Purge ALL verified RecentClips from recording drive
  -pd, --purge-days <DAYS>   Purge verified footage across all folders older than DAYS
  -pt, --purge-to <PCT>      Purge oldest verified RecentClips until drive reaches PCT% full
  -tgt,--target <drive>      Specify target drive (jowua, usb, or 2tb)
  -n,  --dry-run             Simulate purge and report reclaimable space without deleting
  -y,  --yes                 Skip standard confirmation prompts

System Diagnostics:
  --check-deps, --doctor     Audit all tool dependencies, versions, and installation guide

Sync & Auto Maintenance (No flags):
  Performs complete multi-drive backup sync to archive SSD (or --localsync directory),
  verified RecentClips retention prune (2 days on USB), and capacity target purge (80% -> 60% on Jowua).
HELP_EOF
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

# Run tool installation if requested
if (( INSTALL_TOOLS_MODE == 1 )); then
  install_tools_to_drives
fi

# Run dependency check
if (( CHECK_DEPS_MODE == 1 )); then
  check_system_requirements 1
  exit 0
else
  check_system_requirements 0
fi

# ==============================================================================
# 1. VOLUME DISCOVERY & CLASSIFICATION (Markers + Capacity Fallback + LocalSync)
# ==============================================================================
VOL_TESLA_USB=""
VOL_JOWUA=""
VOL_2TB=""

# 1. Resolve Local Sync Destination Override with New Directory Confirmation
if [[ -n "$LOCAL_SYNC_DIR" ]]; then
  LOCAL_SYNC_DIR="${LOCAL_SYNC_DIR/#\~/$HOME}"
  if [[ ! -d "$LOCAL_SYNC_DIR/TeslaCam" ]]; then
    echo "======================================================"
    echo "             📁 Local Archive Destination             "
    echo "======================================================"
    echo "Target Directory: $LOCAL_SYNC_DIR"
    echo "Status: Destination appears new (TeslaCam directory not found)."
    echo "======================================================"
    if (( ASSUME_YES == 0 && STATUS_MODE == 0 )); then
      printf "Confirm initializing archive and syncing to '%s'? [y/N]: " "$LOCAL_SYNC_DIR"
      read -r confirm_dest
      if [[ "$confirm_dest" != [yY] && "$confirm_dest" != [yY][eE][sS] ]]; then
        echo "Sync cancelled by user."
        exit 0
      fi
    fi
    mkdir -p "$LOCAL_SYNC_DIR/TeslaCam"
  fi
  VOL_2TB="$LOCAL_SYNC_DIR"
fi

# 2. Resolve Custom Source Path / Drive Override
if [[ -n "$CUSTOM_SOURCE" ]]; then
  CUSTOM_SOURCE="${CUSTOM_SOURCE/#\~/$HOME}"
  case "$CUSTOM_SOURCE" in
    usb|128gb|256gb)
      for vol in /Volumes/*; do
        [[ -d "$vol/TeslaCam" ]] || continue
        if [[ -d "$vol/TESLA_USB_128GB" || -d "$vol/TESLA_USB_256GB" || -d "$vol/TESLA_USB" ]]; then
          VOL_TESLA_USB="$vol"
          break
        fi
        TOTAL_GB=$(df -g "$vol" 2>/dev/null | awk 'NR==2 {print $2}' || echo 0)
        if (( TOTAL_GB > 30 && TOTAL_GB <= 600 )); then
          VOL_TESLA_USB="$vol"
          break
        fi
      done
      ;;
    jowua|1tb)
      for vol in /Volumes/*; do
        [[ -d "$vol/TeslaCam" ]] || continue
        if [[ -d "$vol/JOWUA_1TB" ]]; then
          VOL_JOWUA="$vol"
          break
        fi
        TOTAL_GB=$(df -g "$vol" 2>/dev/null | awk 'NR==2 {print $2}' || echo 0)
        if (( TOTAL_GB >= 700 && TOTAL_GB <= 1600 )); then
          VOL_JOWUA="$vol"
          break
        fi
      done
      ;;
    *)
      SRC_PATH="$CUSTOM_SOURCE"
      if [[ -d "$SRC_PATH/TeslaCam" ]]; then
        VOL_TESLA_USB="$SRC_PATH"
      elif [[ "$(basename "$SRC_PATH")" == "TeslaCam" && -d "$SRC_PATH" ]]; then
        VOL_TESLA_USB="$(dirname "$SRC_PATH")"
      elif [[ -d "/Volumes/$SRC_PATH/TeslaCam" ]]; then
        VOL_TESLA_USB="/Volumes/$SRC_PATH"
      else
        echo "ERROR: Source '$CUSTOM_SOURCE' does not contain a 'TeslaCam' directory." >&2
        echo "       Please provide a path or drive with an active TeslaCam folder." >&2
        exit 1
      fi
      ;;
  esac
fi

# 3. Auto-discover Connected Volumes if not explicitly set
for vol in /Volumes/*; do
  [[ -d "$vol" && -d "$vol/TeslaCam" ]] || continue

  # Prevent macOS Spotlight from indexing and locking Tesla drives during unmounts
  touch "$vol/.metadata_never_index" 2>/dev/null || true

  # Primary identification via root identity markers
  if [[ -z "$VOL_2TB" && -d "$vol/ARCHIVE_2TB" ]]; then
    VOL_2TB="$vol"
  elif [[ -z "$VOL_JOWUA" && -d "$vol/JOWUA_1TB" ]]; then
    VOL_JOWUA="$vol"
  elif [[ -z "$VOL_TESLA_USB" && ( -d "$vol/TESLA_USB_128GB" || -d "$vol/TESLA_USB_256GB" || -d "$vol/TESLA_USB" ) ]]; then
    VOL_TESLA_USB="$vol"
  else
    # Fallback to partition capacity heuristics
    TOTAL_GB=$(df -g "$vol" 2>/dev/null | awk 'NR==2 {print $2}' || echo 0)
    if (( TOTAL_GB > 30 && TOTAL_GB <= 600 )) && [[ -z "$VOL_TESLA_USB" ]]; then
      VOL_TESLA_USB="$vol"
    elif (( TOTAL_GB >= 700 && TOTAL_GB <= 1600 )) && [[ -z "$VOL_JOWUA" ]]; then
      VOL_JOWUA="$vol"
    elif (( TOTAL_GB > 1600 )) && [[ -z "$VOL_2TB" ]]; then
      VOL_2TB="$vol"
    fi
  fi
done

# 4. Interactive fallback if standard TESLADRIVE volumes are NOT auto-detected
if [[ -z "$VOL_TESLA_USB" && -z "$VOL_JOWUA" && -z "$VOL_2TB" && -t 0 && ASSUME_YES == 0 ]]; then
  echo "=========================================================================="
  echo "                🔍 No 'TESLADRIVE' Volumes Auto-Detected                 "
  echo "=========================================================================="
  echo "Tesla recording drives typically mount under /Volumes as 'TESLADRIVE'."
  echo "If your drive has a custom volume name or is on a local/network path,"
  echo "you can specify it below (or use --source and --localsync)."
  echo "--------------------------------------------------------------------------"
  printf "Enter path to source TeslaCam drive/folder [or press Enter to exit]: "
  read -r prompt_src
  if [[ -n "$prompt_src" ]]; then
    prompt_src="${prompt_src/#\~/$HOME}"
    if [[ -d "$prompt_src/TeslaCam" ]]; then
      VOL_TESLA_USB="$prompt_src"
    elif [[ "$(basename "$prompt_src")" == "TeslaCam" && -d "$prompt_src" ]]; then
      VOL_TESLA_USB="$(dirname "$prompt_src")"
    elif [[ -d "/Volumes/$prompt_src/TeslaCam" ]]; then
      VOL_TESLA_USB="/Volumes/$prompt_src"
    else
      echo "ERROR: Directory '$prompt_src' does not contain a 'TeslaCam' folder." >&2
      exit 1
    fi
  fi
fi

# ==============================================================================
# HELPER: STORAGE TABLE & DAILY TIMELINE ANALYZER
# ==============================================================================
print_drive_summary_and_timeline() {
  local src_dir="$1"
  local dst_dir="$2"
  local mode="${3:-0}"
  [[ -d "$src_dir/TeslaCam" ]] || return

  python3 - "$src_dir" "${dst_dir:-none}" "$mode" << 'PY_EOF'
import os
import sys
import re
import json
from collections import defaultdict
from datetime import datetime, timedelta

src_dir = sys.argv[1]
dst_dir = sys.argv[2] if sys.argv[2] != "none" and os.path.isdir(sys.argv[2]) else None
mode = int(sys.argv[3])

categories = [
    ("RecentClips", "Driving Loop"),
    ("SavedClips", "Honks & Taps"),
    ("SentryClips", "Sentry Alerts"),
    ("Photobooth", "Photo Pictures"),
    ("EncryptedClips", "2026.20+")
]

ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")

def format_hhmmss(seconds):
    sec = int(round(seconds))
    if sec < 0:
        sec = 0
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def clean_reason(reason):
    if not reason:
        return ""
    r = reason.replace("sentry_aware_", "").replace("user_interaction_", "")
    r = r.replace("dashcam_launcher_action_tapped", "dashcam_tapped")
    r = r.replace("vehicle_auto_emergency_braking", "auto_emergency_braking")
    return r

def get_dir_stats(src_path, dst_path):
    src_bytes = 0
    verified_bytes = 0
    for root, _, files in os.walk(src_path):
        for f in files:
            src_fp = os.path.join(root, f)
            try:
                sz = os.path.getsize(src_fp)
                src_bytes += sz
                if dst_path:
                    rel_p = os.path.relpath(src_fp, src_path)
                    dst_fp = os.path.join(dst_path, rel_p)
                    if os.path.exists(dst_fp) and os.path.getsize(dst_fp) >= sz > 0:
                        verified_bytes += sz
            except OSError:
                pass
    return src_bytes, verified_bytes

def format_size(bytes_val):
    for unit in ['B', 'K', 'M', 'G', 'T']:
        if bytes_val < 1024.0 or unit == 'T':
            if unit in ['B', 'K']:
                return f"{int(bytes_val)} {unit}"
            return f"{bytes_val:.1f} {unit}B"
        bytes_val /= 1024.0

def analyze_recent_by_day(dir_path):
    if not os.path.isdir(dir_path):
        return 0, {}
    timestamps = set()
    for root, _, files in os.walk(dir_path):
        for f in files:
            m = ts_pattern.match(f)
            if m:
                try:
                    dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}", "%Y-%m-%d %H:%M:%S")
                    timestamps.add(dt)
                except ValueError:
                    pass
    sorted_ts = sorted(list(timestamps))
    if not sorted_ts:
        return 0, {}
        
    total_segs = len(sorted_ts)
    sessions = []
    curr_start = sorted_ts[0]
    curr_last = sorted_ts[0]
    GAP_THRESHOLD = 120
    for ts in sorted_ts[1:]:
        if (ts - curr_last).total_seconds() <= GAP_THRESHOLD:
            curr_last = ts
        else:
            sessions.append((curr_start, curr_last + timedelta(seconds=60)))
            curr_start = ts
            curr_last = ts
    sessions.append((curr_start, curr_last + timedelta(seconds=60)))
    
    by_day = defaultdict(list)
    for s_start, s_end in sessions:
        date_str = s_start.strftime("%Y-%m-%d")
        dur_sec = (s_end - s_start).total_seconds()
        by_day[date_str].append((s_start, s_end, dur_sec))
        
    return total_segs, by_day

def analyze_events_by_day(dir_path):
    if not os.path.isdir(dir_path):
        return 0, 0, {}
    subdirs = sorted([os.path.join(dir_path, d) for d in os.listdir(dir_path) if os.path.isdir(os.path.join(dir_path, d))])
    total_segs = 0
    by_day = defaultdict(list)
    
    for sd in subdirs:
        timestamps = set()
        event_json_path = os.path.join(sd, "event.json")
        event_data = {}
        if os.path.exists(event_json_path):
            try:
                with open(event_json_path, 'r', encoding='utf-8') as f:
                    event_data = json.load(f)
            except Exception:
                pass
        for root, _, files in os.walk(sd):
            for f in files:
                m = ts_pattern.match(f)
                if m:
                    try:
                        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}", "%Y-%m-%d %H:%M:%S")
                        timestamps.add(dt)
                    except ValueError:
                        pass
        dir_name = os.path.basename(sd)
        m_dir = ts_pattern.match(dir_name)
        if m_dir:
            date_str = m_dir.group(1)
            time_str = f"{m_dir.group(2)}:{m_dir.group(3)}:{m_dir.group(4)}"
        elif timestamps:
            s_first = sorted(list(timestamps))[0]
            date_str = s_first.strftime("%Y-%m-%d")
            time_str = s_first.strftime("%H:%M:%S")
        else:
            date_str = dir_name[:10] if len(dir_name) >= 10 else "Unknown"
            time_str = dir_name[11:] if len(dir_name) > 11 else ""
            
        num_segs = len(timestamps)
        total_segs += num_segs
        dur_sec = num_segs * 60 if num_segs > 0 else 60
        if len(timestamps) > 1:
            s_ts = sorted(list(timestamps))
            dur_sec = max(dur_sec, int((s_ts[-1] - s_ts[0]).total_seconds() + 60))
        dur_str = format_hhmmss(dur_sec)
        
        reason = clean_reason(event_data.get("reason", ""))
        city = event_data.get("city", "").strip()
        street = event_data.get("street", "").strip()
        loc_parts = [p for p in [street, city] if p]
        loc_str = ", ".join(loc_parts) if loc_parts else ""
        
        desc_parts = []
        if reason:
            desc_parts.append(reason)
        if loc_str:
            desc_parts.append(loc_str)
        desc_parts.append(dur_str)
        
        by_day[date_str].append((time_str, dur_sec, ' '.join(desc_parts)))
        
    return len(subdirs), total_segs, by_day

# 1. Gather Storage Table Data
st = os.statvfs(src_dir)
total_disk_b = st.f_blocks * st.f_frsize
free_disk_b = st.f_bfree * st.f_frsize
used_disk_b = total_disk_b - free_disk_b

cat_data = []
for cat, desc in categories:
    src_cat_p = os.path.join(src_dir, "TeslaCam", cat)
    dst_cat_p = os.path.join(dst_dir, "TeslaCam", cat) if dst_dir else None
    if os.path.isdir(src_cat_p):
        sz, v_sz = get_dir_stats(src_cat_p, dst_cat_p)
        cat_data.append((cat, desc, sz, v_sz))

drive_label = "1TB Jowua Hub" if os.path.exists(os.path.join(src_dir, "JOWUA_1TB")) or "JOWUA" in src_dir else ("2TB Archive SSD" if os.path.exists(os.path.join(src_dir, "ARCHIVE_2TB")) else ("Tesla USB" if os.path.exists(os.path.join(src_dir, "TESLA_USB_128GB")) or "TESLADRIVE" in src_dir else os.path.basename(src_dir)))

# 2. Print Perfectly Aligned Structured Table (82 Characters Total Width)
print(f"┌{'─'*80}┐")
hdr_text = f" Storage Breakdown: {drive_label}"
print(f"│{hdr_text:<80}│")
print(f"├{'─'*32}┬{'─'*12}┬{'─'*11}┬{'─'*22}┤")
print(f"│ {'Category':<30} │ {'Size':<10} │ {'% Drive':<9} │ {'Archive Status':<20} │")
print(f"├{'─'*32}┼{'─'*12}┼{'─'*11}┼{'─'*22}┤")

for cat, desc, sz, v_sz in cat_data:
    pct_val = (sz * 100.0) / total_disk_b if total_disk_b > 0 else 0.0
    if pct_val >= 1.0:
        pct_str = f"{pct_val:.0f}%"
    elif pct_val > 0.05:
        pct_str = f"{pct_val:.1f}%"
    else:
        pct_str = "<0.1%"
        
    label_full = f"{cat} ({desc})"
    if len(label_full) > 30:
        label_full = label_full[:30]
    
    if dst_dir and src_dir == dst_dir:
        archive_status = "Primary Archive"
    elif not dst_dir:
        archive_status = "Archive Disconnected"
    elif sz == 0:
        archive_status = "Empty"
    elif v_sz >= sz:
        archive_status = "✔ 100% Archived"
    else:
        v_pct = (v_sz * 100.0) / sz
        archive_status = f"✔ {v_pct:.0f}% Archived"
        
    print(f"│ {label_full:<30} │ {format_size(sz):>10} │ {pct_str:>9} │ {archive_status:<20} │")

print(f"├{'─'*32}┴{'─'*12}┴{'─'*11}┴{'─'*22}┤")
used_str = format_size(used_disk_b)
free_str = format_size(free_disk_b)
total_disk_str = format_size(total_disk_b)
pct_disk = f"{(used_disk_b * 100.0) / total_disk_b:.0f}%"
tot_text = f" Total Partition: {used_str} used / {free_str} free ({pct_disk} full of {total_disk_str})"
print(f"│{tot_text:<80}│")
print(f"└{'─'*80}┘")

# 3. Print Daily Timeline if mode > 0
if mode > 0:
    base_dir = os.path.join(src_dir, "TeslaCam")
    print(f"\n   📅 Timeline Breakdown ({'Expanded' if mode==2 else 'Daily Summary'}):")
    for cat in ["RecentClips", "SavedClips", "SentryClips", "Photobooth"]:
        cat_path = os.path.join(base_dir, cat)
        if not os.path.isdir(cat_path):
            continue
        if cat == "RecentClips":
            r_segs, r_days = analyze_recent_by_day(cat_path)
            print(f"       - {cat:<18} : ({r_segs} segments)")
            for d in sorted(r_days.keys()):
                sessions = r_days[d]
                total_dur = sum(s[2] for s in sessions)
                s_cnt = len(sessions)
                s_word = "drive" if s_cnt == 1 else "drives"
                print(f"         └─ {d} ({s_cnt} {s_word}, {format_hhmmss(total_dur)})")
                if mode == 2:
                    for s_start, s_end, s_dur in sessions:
                        print(f"              └─ {s_start.strftime('%H:%M:%S')} - {s_end.strftime('%H:%M:%S')} ({format_hhmmss(s_dur)})")
        elif cat in ["SavedClips", "SentryClips"]:
            ev_cnt, sv_segs, sv_days = analyze_events_by_day(cat_path)
            ev_str = f"{ev_cnt} event" if ev_cnt == 1 else f"{ev_cnt} events"
            print(f"       - {cat:<18} : ({ev_str}, {sv_segs} segments)")
            for d in sorted(sv_days.keys()):
                events = sv_days[d]
                total_dur = sum(e[1] for e in events)
                e_cnt = len(events)
                e_word = "event" if e_cnt == 1 else "events"
                print(f"         └─ {d} ({e_cnt} {e_word}, {format_hhmmss(total_dur)})")
                if mode == 2:
                    for t_str, _, desc in events:
                        print(f"              └─ {t_str} ({desc})")
    
    enc_path = os.path.join(base_dir, "EncryptedClips")
    if os.path.isdir(enc_path):
        r_segs, r_days = analyze_recent_by_day(os.path.join(enc_path, "RecentClips"))
        sv_ev, sv_segs, sv_days = analyze_events_by_day(os.path.join(enc_path, "SavedClips"))
        sn_ev, sn_segs, sn_days = analyze_events_by_day(os.path.join(enc_path, "SentryClips"))
        total_enc_events = sv_ev + sn_ev
        total_enc_segs = r_segs + sv_segs + sn_segs
        ev_total_str = f"{total_enc_events} event" if total_enc_events == 1 else f"{total_enc_events} events"
        print(f"       - {'EncryptedClips':<18} : ({ev_total_str}, {total_enc_segs} segments)")
        for sub, ev, segs, days, is_rec in [
            ("RecentClips", 0, r_segs, r_days, True),
            ("SavedClips", sv_ev, sv_segs, sv_days, False),
            ("SentryClips", sn_ev, sn_segs, sn_days, False)
        ]:
            sub_p = os.path.join(enc_path, sub)
            if os.path.isdir(sub_p):
                if is_rec:
                    print(f"         └─ {sub:<15}: ({segs} segments)")
                    for d in sorted(days.keys()):
                        sessions = days[d]
                        total_dur = sum(s[2] for s in sessions)
                        s_cnt = len(sessions)
                        s_word = "drive" if s_cnt == 1 else "drives"
                        print(f"           └─ {d} ({s_cnt} {s_word}, {format_hhmmss(total_dur)})")
                        if mode == 2:
                            for s_start, s_end, s_dur in sessions:
                                print(f"                └─ {s_start.strftime('%H:%M:%S')} - {s_end.strftime('%H:%M:%S')} ({format_hhmmss(s_dur)})")
                else:
                    sub_ev_str = f"{ev} event" if ev == 1 else f"{ev} events"
                    print(f"         └─ {sub:<15}: ({sub_ev_str}, {segs} segments)")
                    for d in sorted(days.keys()):
                        events = days[d]
                        total_dur = sum(e[1] for e in events)
                        e_cnt = len(events)
                        e_word = "event" if e_cnt == 1 else "events"
                        print(f"           └─ {d} ({e_cnt} {e_word}, {format_hhmmss(total_dur)})")
                        if mode == 2:
                            for t_str, _, desc in events:
                                print(f"                └─ {t_str} ({desc})")
PY_EOF
}

# ==============================================================================
# PURGE ENGINE: SAFE VERIFIED OR INTERACTIVE FORCE PURGE
# ==============================================================================
execute_safe_or_force_purge() {
  local src="$1"
  local dst="$2"
  local scope="$3"     # "recent_only", "all_folders", or "capacity"
  local param="$4"     # days (e.g. 2, 5, 0) OR target_pct (e.g. 60)
  local is_force="${5:-0}"
  local is_dry="${6:-0}"
  local skip_confirm="${7:-0}"

  local src_label="$(basename "$src")"
  if [[ -d "$src/ARCHIVE_2TB" ]]; then
    src_label="2TB Archive SSD"
  elif [[ -d "$src/JOWUA_1TB" ]]; then
    src_label="1TB Jowua Hub"
  elif [[ -d "$src/TESLA_USB_128GB" || -d "$src/TESLA_USB_256GB" || -d "$src/TESLA_USB" ]]; then
    src_label="Tesla USB"
  fi

  # If purging the destination archive drive/folder itself, force mode is mandatory
  if [[ -n "$VOL_2TB" && "$src" == "$VOL_2TB" ]]; then
    is_force=1
  fi

  # If Archive Destination is missing and force is NOT set, block execution
  if (( is_force == 0 )) && [[ -z "$dst" || ! -d "$dst/TeslaCam" ]]; then
    echo "SAFETY BLOCK: Master Archive Destination is NOT connected or configured."
    echo "              Nothing will be purged from $src_label."
    echo "              (To delete without archive verification, use interactive Force Purge: -pf)"
    return 0
  fi

  # Run Python scanner (verified or force mode)
  local scan_result
  scan_result=$(python3 - "$src" "${dst:-none}" "$scope" "$param" "$is_force" << 'PY_EOF'
import os
import sys
import re
from datetime import datetime, timedelta

src_dir = sys.argv[1]
dst_dir = sys.argv[2]
scope = sys.argv[3]
param = sys.argv[4]
is_force = sys.argv[5] == "1"

ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")

search_paths = []
if scope == "recent_only":
    for sub in ["RecentClips", "EncryptedClips/RecentClips"]:
        p = os.path.join(src_dir, "TeslaCam", sub)
        if os.path.isdir(p):
            search_paths.append(p)
else:
    search_paths.append(os.path.join(src_dir, "TeslaCam"))

cutoff_date = None
bytes_to_free = 0

if scope == "capacity":
    target_pct = int(param)
    total_b = 0
    used_b = 0
    try:
        st = os.statvfs(src_dir)
        total_b = st.f_blocks * st.f_frsize
        free_b = st.f_bfree * st.f_frsize
        used_b = total_b - free_b
        cur_pct = (used_b * 100) / total_b if total_b > 0 else 0
        if cur_pct < 80 and target_pct >= 60 and not is_force:
            print("0|0.00|0|below_threshold")
            sys.exit(0)
        target_used_b = (total_b * target_pct) / 100
        bytes_to_free = max(0, used_b - target_used_b)
    except Exception:
        bytes_to_free = 0
else:
    days = int(param)
    if days > 0:
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

all_candidates = []
for sp in search_paths:
    for root, _, files in os.walk(sp):
        for f in files:
            if not f.endswith(".mp4"):
                continue
            src_fp = os.path.join(root, f)
            m = ts_pattern.match(f)
            fdate = None
            if m:
                fdate = m.group(1)
            else:
                p_name = os.path.basename(root)
                m_p = ts_pattern.match(p_name)
                if m_p:
                    fdate = m_p.group(1)
                    
            if cutoff_date is None or (fdate and fdate < cutoff_date):
                all_candidates.append((src_fp, fdate or ""))

all_candidates.sort(key=lambda x: (x[1], x[0]))

eligible_files = []
total_bytes = 0
unverified_count = 0

for src_fp, _ in all_candidates:
    try:
        src_sz = os.path.getsize(src_fp)
        if is_force:
            eligible_files.append((src_fp, src_sz))
            total_bytes += src_sz
            if scope == "capacity" and bytes_to_free > 0 and total_bytes >= bytes_to_free:
                break
        else:
            rel_path = os.path.relpath(src_fp, os.path.join(src_dir, "TeslaCam"))
            dst_fp = os.path.join(dst_dir, "TeslaCam", rel_path)
            if os.path.exists(dst_fp) and os.path.getsize(dst_fp) >= src_sz > 0:
                eligible_files.append((src_fp, src_sz))
                total_bytes += src_sz
                if scope == "capacity" and bytes_to_free > 0 and total_bytes >= bytes_to_free:
                    break
            else:
                unverified_count += 1
    except OSError:
        unverified_count += 1

gb_freed = total_bytes / (1024.0**3)
print(f"{len(eligible_files)}|{gb_freed:.2f}|{unverified_count}|ok")
PY_EOF
)

  local count=$(echo "$scan_result" | awk -F'|' '{print $1}')
  local gb=$(echo "$scan_result" | awk -F'|' '{print $2}')
  local unverified=$(echo "$scan_result" | awk -F'|' '{print $3}')
  local status_tag=$(echo "$scan_result" | awk -F'|' '{print $4}')

  if [[ "$status_tag" == "below_threshold" ]]; then
    echo ">>> Capacity Check: $src_label"
    echo "    Status: Below purge threshold (no purge required)."
    return 0
  fi

  if (( count == 0 )); then
    echo ">>> Purge Check: $src_label"
    echo "    Status: 0 clips eligible for purge."
    if (( unverified > 0 )); then
      echo "    Notice: $unverified clips protected (not yet verified in master archive)."
    fi
    return 0
  fi

  if (( is_force == 1 )); then
    echo ""
    echo "======================================================"
    echo "          ⚠️  FORCE PURGE WARNING  ⚠️                 "
    echo "======================================================"
    echo "Target Drive : $src_label [$src]"
    echo "Scope        : $scope ($param)"
    echo "Eligible     : $count clips ($gb GB)"
    echo ""
    echo "CAUTION: This will PERMANENTLY DELETE footage from this device"
    echo "         WITHOUT requiring archive verification!"
    echo "======================================================"
  else
    echo ">>> Verified Purge Scan: $src_label"
    echo "    Archive Verified      : $count clips ($gb GB) confirmed in archive"
    if (( unverified > 0 )); then
      echo "    Protected (Unverified): $unverified clips (preserved because NOT yet archived)"
    fi
  fi

  if (( is_dry == 1 )); then
    echo "    [DRY-RUN] Space that would be reclaimed: $gb GB. No files deleted."
    return 0
  fi

  # Confirmation Gate
  if (( is_force == 1 )); then
    printf "Type 'FORCE' in capital letters to confirm permanent deletion: "
    read -r force_confirm
    if [[ "$force_confirm" != "FORCE" ]]; then
      echo "Force purge aborted. Confirmation keyword did not match."
      return 0
    fi
  elif (( skip_confirm == 0 && ASSUME_YES == 0 )); then
    printf "    Proceed with verified deletion on %s (%s GB)? [y/N]: " "$src_label" "$gb"
    read -r confirm_ans
    if [[ "$confirm_ans" != [yY] && "$confirm_ans" != [yY][eE][sS] ]]; then
      echo "    Purge cancelled by user."
      return 0
    fi
  fi

  # Execute deletion
  python3 - "$src" "${dst:-none}" "$scope" "$param" "$is_force" << 'PY_EOF'
import os
import sys
import re
from datetime import datetime, timedelta

src_dir = sys.argv[1]
dst_dir = sys.argv[2]
scope = sys.argv[3]
param = sys.argv[4]
is_force = sys.argv[5] == "1"

ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")

search_paths = []
if scope == "recent_only":
    for sub in ["RecentClips", "EncryptedClips/RecentClips"]:
        p = os.path.join(src_dir, "TeslaCam", sub)
        if os.path.isdir(p):
            search_paths.append(p)
else:
    search_paths.append(os.path.join(src_dir, "TeslaCam"))

cutoff_date = None
bytes_to_free = 0
if scope == "capacity":
    target_pct = int(param)
    try:
        st = os.statvfs(src_dir)
        total_b = st.f_blocks * st.f_frsize
        free_b = st.f_bfree * st.f_frsize
        used_b = total_b - free_b
        target_used_b = (total_b * target_pct) / 100
        bytes_to_free = max(0, used_b - target_used_b)
    except Exception:
        bytes_to_free = 0
else:
    days = int(param)
    if days > 0:
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

all_candidates = []
for sp in search_paths:
    for root, _, files in os.walk(sp):
        for f in files:
            if not f.endswith(".mp4"):
                continue
            src_fp = os.path.join(root, f)
            m = ts_pattern.match(f)
            fdate = None
            if m:
                fdate = m.group(1)
            else:
                p_name = os.path.basename(root)
                m_p = ts_pattern.match(p_name)
                if m_p:
                    fdate = m_p.group(1)
                    
            if cutoff_date is None or (fdate and fdate < cutoff_date):
                all_candidates.append((src_fp, fdate or ""))

all_candidates.sort(key=lambda x: (x[1], x[0]))

freed_bytes = 0
deleted_count = 0

for src_fp, _ in all_candidates:
    try:
        src_sz = os.path.getsize(src_fp)
        allow_delete = False
        if is_force:
            allow_delete = True
        else:
            rel_path = os.path.relpath(src_fp, os.path.join(src_dir, "TeslaCam"))
            dst_fp = os.path.join(dst_dir, "TeslaCam", rel_path)
            if os.path.exists(dst_fp) and os.path.getsize(dst_fp) >= src_sz > 0:
                allow_delete = True
                
        if allow_delete:
            os.remove(src_fp)
            deleted_count += 1
            freed_bytes += src_sz
            
            for ext in [".json", ".png"]:
                src_comp = os.path.splitext(src_fp)[0] + ext
                if os.path.exists(src_comp):
                    if is_force:
                        try:
                            os.remove(src_comp)
                        except OSError:
                            pass
                    else:
                        rel_comp = os.path.relpath(src_comp, os.path.join(src_dir, "TeslaCam"))
                        dst_comp = os.path.join(dst_dir, "TeslaCam", rel_comp)
                        if os.path.exists(dst_comp) and os.path.getsize(dst_comp) > 0:
                            try:
                                os.remove(src_comp)
                            except OSError:
                                pass
                                
            if scope == "capacity" and bytes_to_free > 0 and freed_bytes >= bytes_to_free:
                break
    except OSError:
        pass

# Clean empty directories
for root, dirs, _ in os.walk(os.path.join(src_dir, "TeslaCam"), topdown=False):
    for d in dirs:
        dp = os.path.join(root, d)
        try:
            if not os.listdir(dp):
                os.rmdir(dp)
        except OSError:
            pass
PY_EOF

  echo "    ✔ Purge complete: Reclaimed $gb GB across $count clips from $src_label."
}

# ==============================================================================
# HELPER: INTERACTIVE PURGE WIZARD (Supports All Drives & Force Mode)
# ==============================================================================
run_interactive_purge_wizard() {
  local force_flag="${1:-0}"

  echo ""
  echo "======================================================"
  if (( force_flag == 1 )); then
    echo "       ⚠️  TeslaCam Interactive FORCE Purge Wizard    "
  else
    echo "          🚗 TeslaCam Interactive Purge Wizard        "
  fi
  echo "======================================================"
  
  if [[ -n "$VOL_2TB" ]]; then
    echo "Archive Status: Master Archive Active [${VOL_2TB}]"
  else
    echo "Archive Status: ⚠️  Master Archive is NOT connected (Force mode only)"
    force_flag=1
  fi
  echo "------------------------------------------------------"
  echo "Select target drive to purge:"
  local options=()
  local opt_idx=1

  if [[ -n "$VOL_JOWUA" ]]; then
    local j_usage=$(df -h "$VOL_JOWUA" | awk 'NR==2 {print $3 " used / " $4 " free"}')
    echo "  [$opt_idx] 1TB Jowua Hub   [$j_usage]"
    options+=("jowua")
    ((opt_idx++))
  fi

  if [[ -n "$VOL_TESLA_USB" ]]; then
    local u_usage=$(df -h "$VOL_TESLA_USB" | awk 'NR==2 {print $3 " used / " $4 " free"}')
    echo "  [$opt_idx] Tesla USB       [$u_usage]"
    options+=("usb")
    ((opt_idx++))
  fi

  if [[ -n "$VOL_2TB" && -d "$VOL_2TB/ARCHIVE_2TB" ]]; then
    local a_usage=$(df -h "$VOL_2TB" | awk 'NR==2 {print $3 " used / " $4 " free"}')
    echo "  [$opt_idx] 2TB Archive SSD [$a_usage] (Requires Force Confirmation)"
    options+=("2tb")
    ((opt_idx++))
  fi

  if [[ -n "$VOL_JOWUA" && -n "$VOL_TESLA_USB" ]]; then
    echo "  [$opt_idx] Multiple Recording Drives (Configure rates separately)"
    options+=("both")
    ((opt_idx++))
  fi
  echo "  [q] Cancel / Exit"
  echo ""
  printf "Enter choice [1-%d or q]: " $((opt_idx - 1))
  read -r drive_choice

  case "$drive_choice" in
    q|Q|exit|cancel)
      echo "Exiting purge wizard."
      exit 0
      ;;
  esac

  local selected_target=""
  if [[ "$drive_choice" =~ ^[0-9]+$ && "$drive_choice" -le ${#options[@]} && "$drive_choice" -ge 1 ]]; then
    selected_target="${options[$drive_choice]}"
  else
    echo "Invalid selection."
    exit 1
  fi

  local drives_to_process=()
  case "$selected_target" in
    jowua)
      drives_to_process+=("$VOL_JOWUA")
      ;;
    usb)
      drives_to_process+=("$VOL_TESLA_USB")
      ;;
    2tb)
      drives_to_process+=("$VOL_2TB")
      ;;
    both)
      drives_to_process+=("$VOL_JOWUA" "$VOL_TESLA_USB")
      ;;
  esac

  for d_vol in "${drives_to_process[@]}"; do
    local d_name="Drive"
    local cur_drive_force=$force_flag

    if [[ "$d_vol" == "$VOL_JOWUA" ]]; then
      d_name="1TB Jowua Hub"
    elif [[ "$d_vol" == "$VOL_TESLA_USB" ]]; then
      d_name="Tesla USB"
    elif [[ "$d_vol" == "$VOL_2TB" ]]; then
      d_name="2TB Archive SSD"
      cur_drive_force=1
    fi

    echo ""
    echo "------------------------------------------------------"
    echo "Configure Purge Rate for: $d_name"
    if (( cur_drive_force == 1 )); then
      echo "Mode: ⚠️  FORCE PURGE (Bypasses archive verification)"
    else
      echo "Mode: 🔒 Safe Verified Purge (Guaranteed by Master Archive)"
    fi
    echo "------------------------------------------------------"
    echo "  [1] Purge RecentClips older than N days (Default: 2 days)"
    echo "  [2] Purge ALL RecentClips (Keep 0 days of RecentClips)"
    echo "  [3] Purge by Target Capacity (e.g. down to 50% full)"
    echo "  [4] Purge ALL footage older than N days (Recent + Saved + Sentry)"
    echo "  [s] Skip this drive"
    echo ""
    printf "Choice [1-4 or s]: "
    read -r rate_choice

    case "$rate_choice" in
      1)
        printf "Enter retention days for RecentClips [default 2]: "
        read -r days_in
        [[ -z "$days_in" ]] && days_in=2
        execute_safe_or_force_purge "$d_vol" "$VOL_2TB" "recent_only" "$days_in" "$cur_drive_force" "$DRY_RUN" 0
        ;;
      2)
        execute_safe_or_force_purge "$d_vol" "$VOL_2TB" "recent_only" "0" "$cur_drive_force" "$DRY_RUN" 0
        ;;
      3)
        printf "Enter target capacity percent [e.g. 50]: "
        read -r pct_in
        [[ -z "$pct_in" ]] && pct_in=50
        execute_safe_or_force_purge "$d_vol" "$VOL_2TB" "capacity" "$pct_in" "$cur_drive_force" "$DRY_RUN" 0
        ;;
      4)
        printf "Enter retention days for ALL folders [default 5]: "
        read -r days_in
        [[ -z "$days_in" ]] && days_in=5
        execute_safe_or_force_purge "$d_vol" "$VOL_2TB" "all_folders" "$days_in" "$cur_drive_force" "$DRY_RUN" 0
        ;;
      s|S|q|Q)
        echo "Skipping $d_name."
        ;;
      *)
        echo "Invalid selection, skipping $d_name."
        ;;
    esac
  done

  echo ""
  echo "======================================================"
  echo "Purge wizard completed."
  echo "======================================================"
  exit 0
}

# ==============================================================================
# 2. STATUS / TIMELINE CHECK MODE (--status / --summary / --timeline / --timeline-full)
# ==============================================================================
if [[ "$STATUS_MODE" -eq 1 && -z "$PURGE_MODE" ]]; then
  if [[ -z "$VOL_TESLA_USB" && -z "$VOL_JOWUA" && -z "$VOL_2TB" ]]; then
    echo "ERROR: No active TeslaCam volumes or archive directories detected." >&2
    echo "       Tesla drives typically mount under /Volumes as 'TESLADRIVE'." >&2
    echo "       To specify a custom path, use: ./tesla_sync.sh --status --source /path/to/drive" >&2
    exit 1
  fi

  echo "=================================================================================="
  echo "                       TeslaCam Storage & Archive Status                          "
  if (( TIMELINE_MODE == 1 )); then
    echo "                          (Daily Timeline Included)                               "
  elif (( TIMELINE_MODE == 2 )); then
    echo "                     (Full Expanded Events Included)                              "
  fi
  echo "=================================================================================="
  echo ""

  if [[ -n "$VOL_TESLA_USB" ]]; then
    print_drive_summary_and_timeline "$VOL_TESLA_USB" "$VOL_2TB" "$TIMELINE_MODE"
    echo ""
  fi

  if [[ -n "$VOL_JOWUA" ]]; then
    print_drive_summary_and_timeline "$VOL_JOWUA" "$VOL_2TB" "$TIMELINE_MODE"
    echo ""
  fi

  if [[ -n "$VOL_2TB" && -d "$VOL_2TB/ARCHIVE_2TB" ]]; then
    print_drive_summary_and_timeline "$VOL_2TB" "$VOL_2TB" "$TIMELINE_MODE"
    echo ""
  elif [[ -n "$LOCAL_SYNC_DIR" ]]; then
    print_drive_summary_and_timeline "$LOCAL_SYNC_DIR" "$LOCAL_SYNC_DIR" "$TIMELINE_MODE"
    echo ""
  fi
  exit 0
fi

# ==============================================================================
# 3. PURGE DISPATCHER (Interactive Wizard or CLI)
# ==============================================================================
if [[ "$PURGE_MODE" == "wizard" || ("$PURGE_MODE" != "" && -z "$PURGE_TARGET_DRIVE" && -t 0) ]]; then
  run_interactive_purge_wizard "$FORCE_MODE"
fi

if [[ -n "$PURGE_MODE" ]]; then
  TARGETS=()
  case "$PURGE_TARGET_DRIVE" in
    jowua|1tb)
      [[ -n "$VOL_JOWUA" ]] && TARGETS+=("$VOL_JOWUA")
      ;;
    usb|128gb|256gb)
      [[ -n "$VOL_TESLA_USB" ]] && TARGETS+=("$VOL_TESLA_USB")
      ;;
    2tb|archive)
      [[ -n "$VOL_2TB" ]] && TARGETS+=("$VOL_2TB")
      ;;
    both|all)
      [[ -n "$VOL_JOWUA" ]] && TARGETS+=("$VOL_JOWUA")
      [[ -n "$VOL_TESLA_USB" ]] && TARGETS+=("$VOL_TESLA_USB")
      ;;
    *)
      if [[ -n "$VOL_JOWUA" && -z "$VOL_TESLA_USB" ]]; then
        TARGETS+=("$VOL_JOWUA")
      elif [[ -n "$VOL_TESLA_USB" && -z "$VOL_JOWUA" ]]; then
        TARGETS+=("$VOL_TESLA_USB")
      elif [[ -n "$VOL_JOWUA" && -n "$VOL_TESLA_USB" ]]; then
        TARGETS+=("$VOL_JOWUA" "$VOL_TESLA_USB")
      elif [[ -n "$VOL_2TB" ]]; then
        TARGETS+=("$VOL_2TB")
      fi
      ;;
  esac

  if (( ${#TARGETS[@]} == 0 )); then
    echo "ERROR: No eligible source drives found for purge." >&2
    exit 1
  fi

  for tgt in "${TARGETS[@]}"; do
    case "$PURGE_MODE" in
      recent)
        execute_safe_or_force_purge "$tgt" "$VOL_2TB" "recent_only" "$PURGE_DAYS" "$FORCE_MODE" "$DRY_RUN" "$ASSUME_YES"
        ;;
      all_recent)
        execute_safe_or_force_purge "$tgt" "$VOL_2TB" "recent_only" "0" "$FORCE_MODE" "$DRY_RUN" "$ASSUME_YES"
        ;;
      days)
        execute_safe_or_force_purge "$tgt" "$VOL_2TB" "all_folders" "$PURGE_DAYS" "$FORCE_MODE" "$DRY_RUN" "$ASSUME_YES"
        ;;
      capacity)
        execute_safe_or_force_purge "$tgt" "$VOL_2TB" "capacity" "$PURGE_TARGET_PCT" "$FORCE_MODE" "$DRY_RUN" "$ASSUME_YES"
        ;;
    esac
    echo ""
  done
  exit 0
fi

# ==============================================================================
# 4. TOPOLOGY & MATRIX VALIDATION (Standard Sync Mode)
# ==============================================================================
if [[ -z "$VOL_TESLA_USB" && -z "$VOL_JOWUA" ]]; then
  echo "ERROR: No active TeslaCam recording drive detected." >&2
  echo "       Tesla drives typically mount under /Volumes as 'TESLADRIVE'." >&2
  echo "       If using custom mount paths, provide: --source /path/to/drive" >&2
  exit 1
fi

# If in Sync mode and no archive destination is detected, prompt for --localsync in interactive terminal
if [[ -z "$VOL_2TB" && -t 0 && ASSUME_YES == 0 ]]; then
  echo "=========================================================================="
  echo "                📁 Master Archive Destination Notice                      "
  echo "=========================================================================="
  echo "No standard 2TB Master Archive SSD was detected under /Volumes."
  echo "You can sync directly to a local folder or NAS directory on this computer."
  echo "--------------------------------------------------------------------------"
  printf "Enter local archive path [e.g. ~/TeslaArchive, or Enter to cancel]: "
  read -r prompt_local_sync
  if [[ -n "$prompt_local_sync" ]]; then
    prompt_local_sync="${prompt_local_sync/#\~/$HOME}"
    mkdir -p "$prompt_local_sync/TeslaCam"
    VOL_2TB="$prompt_local_sync"
    LOCAL_SYNC_DIR="$prompt_local_sync"
  else
    echo "No archive destination specified. Sync cancelled."
    exit 0
  fi
fi

# ==============================================================================
# HELPER: DYNAMIC CUSTOM PROGRESS MONITOR
# ==============================================================================
sync_volumes() {
  local src="$1"
  local dst="$2"
  local label="$3"

  echo ">>> Starting sync: $label"
  echo "    Source: $src/TeslaCam/"
  echo "    Target: $dst/TeslaCam/"

  # 1. Calculate bytes to transfer via dry-run (strictly matching execution flags)
  printf "    Calculating transfer payload..."
  local dry_run_out
  dry_run_out=$("$RSYNC_BIN" -a --dry-run --stats \
    --modify-window=2 \
    --exclude=".Spotlight-V100" \
    --exclude=".Trashes" \
    --exclude="System Volume Information" \
    --exclude=".fseventsd" \
    --exclude=".*" \
    --exclude="._*" \
    --exclude="Tools" \
    --exclude="Icons" \
    --exclude="TeslaCam_Exports" \
    --exclude="TeslaCam_Archive" \
    "$src/TeslaCam/" "$dst/TeslaCam/" 2>/dev/null)

  local to_transfer_bytes
  to_transfer_bytes=$(echo "$dry_run_out" | awk '/Total transferred file size:/ {print $5}' | tr -d ',')
  [[ -z "$to_transfer_bytes" ]] && to_transfer_bytes=0

  local to_transfer_kb=$(( to_transfer_bytes / 1024 ))

  if (( to_transfer_kb == 0 )); then
    printf "\r\033[K    Target is already up-to-date (0 bytes to sync).\n"
    return 0
  fi

  local to_transfer_gb=$(printf "%.2f" "$(( to_transfer_kb / 1048576.0 ))")
  printf "\r\033[K    Payload: %s GB to sync.\n" "$to_transfer_gb"

  # Baseline destination disk usage
  local initial_dst_kb=$(df -k "$dst" | awk 'NR==2 {print $3}')
  local prev_dst_kb=$initial_dst_kb

  # 2. Launch Rsync in background with --stats
  "$RSYNC_BIN" -a \
    --modify-window=2 \
    --stats \
    --exclude=".Spotlight-V100" \
    --exclude=".Trashes" \
    --exclude="System Volume Information" \
    --exclude=".fseventsd" \
    --exclude=".*" \
    --exclude="._*" \
    --exclude="Tools" \
    --exclude="Icons" \
    --exclude="TeslaCam_Exports" \
    --exclude="TeslaCam_Archive" \
    "$src/TeslaCam/" "$dst/TeslaCam/" > "$LOG_FILE" 2>&1 &
  local rsync_pid=$!

  # 3. Live progress updater polling loop
  while kill -0 $rsync_pid 2>/dev/null; do
    sleep 2
    local cur_dst_kb=$(df -k "$dst" | awk 'NR==2 {print $3}')
    local session_copied_kb=$(( cur_dst_kb - initial_dst_kb ))
    (( session_copied_kb < 0 )) && session_copied_kb=0

    local diff_kb=$(( cur_dst_kb - prev_dst_kb ))
    (( diff_kb < 0 )) && diff_kb=0
    prev_dst_kb=$cur_dst_kb

    local session_copied_gb=$(printf "%.2f" "$(( session_copied_kb / 1048576.0 ))")
    local pct=$(printf "%.1f" "$(( (session_copied_kb * 100.0) / to_transfer_kb ))")
    (( $(echo "$pct > 100.0" | bc -l 2>/dev/null || echo 0) )) && pct="99.9"

    local speed_mb=$(printf "%.2f" "$(( (diff_kb / 1024.0) / 2.0 ))")
    local eta_str="--:--"

    if (( diff_kb > 0 )); then
      local remaining_kb=$(( to_transfer_kb - session_copied_kb ))
      if (( remaining_kb > 0 )); then
        local eta_sec=$(printf "%.0f" "$(( remaining_kb / (diff_kb / 2.0) ))")
        if (( eta_sec > 60 )); then
          local eta_min=$(printf "%.1f" "$(( eta_sec / 60.0 ))")
          eta_str="${eta_min}m"
        else
          eta_str="${eta_sec}s"
        fi
      fi
    fi

    printf "\r\033[K    Transfer: %s%% (%s GB / %s GB) | Speed: %s MB/s | ETA: %s" "$pct" "$session_copied_gb" "$to_transfer_gb" "$speed_mb" "$eta_str"
  done

  wait $rsync_pid
  local exit_code=$?

  local actual_bytes=$(awk '/Total transferred file size:/ {print $5}' "$LOG_FILE" 2>/dev/null | tr -d ',')
  local actual_gb="0.00"
  if [[ -n "$actual_bytes" && "$actual_bytes" -gt 0 ]]; then
    actual_gb=$(printf "%.2f" "$(( actual_bytes / (1024.0 * 1048576.0) ))")
  fi

  if (( exit_code == 0 )); then
    if (( $(echo "$actual_gb > 0" | bc -l 2>/dev/null || echo 0) )); then
      printf "\r\033[K    Transfer: 100.0%% (%s GB transferred) | Complete.\n" "$actual_gb"
    else
      printf "\r\033[K    Transfer: 100.0%% (0.00 GB transferred) | Up-to-date.\n"
    fi
  else
    printf "\r\033[K    Transfer encountered an issue. Check %s\n" "$LOG_FILE"
  fi
}

# ==============================================================================
# 5. WORKFLOW EXECUTION MATRIX (Default Sync & Prune)
# ==============================================================================
echo "======================================================"
echo "          TeslaCam Multi-Drive Sync & Prune           "
echo "======================================================"

# Target: Local directory (--localsync)
if [[ -n "$LOCAL_SYNC_DIR" ]]; then
  echo "[Target: Local Directory] ${LOCAL_SYNC_DIR}"
  echo ""
  if [[ -n "$VOL_TESLA_USB" ]]; then
    sync_volumes "$VOL_TESLA_USB" "$LOCAL_SYNC_DIR" "Tesla USB -> Local Archive"
    echo ""
    execute_safe_or_force_purge "$VOL_TESLA_USB" "$LOCAL_SYNC_DIR" "recent_only" 2 0 0 1
  fi
  if [[ -n "$VOL_JOWUA" ]]; then
    sync_volumes "$VOL_JOWUA" "$LOCAL_SYNC_DIR" "JOWUA Hub -> Local Archive"
    echo ""
    execute_safe_or_force_purge "$VOL_JOWUA" "$LOCAL_SYNC_DIR" "capacity" 60 0 0 1
  fi

# All 3 Connected (Tesla USB + JOWUA + 2TB Archive SSD)
elif [[ -n "$VOL_TESLA_USB" && -n "$VOL_JOWUA" && -n "$VOL_2TB" ]]; then
  echo "[Detected] Tesla USB + JOWUA + 2TB Archive SSD detected."
  echo ""
  sync_volumes "$VOL_TESLA_USB" "$VOL_2TB" "Tesla USB -> 2TB Archive"
  echo ""
  sync_volumes "$VOL_TESLA_USB" "$VOL_JOWUA" "Tesla USB -> JOWUA 1TB"
  echo ""
  sync_volumes "$VOL_JOWUA" "$VOL_2TB" "JOWUA 1TB -> 2TB Archive"
  echo ""
  execute_safe_or_force_purge "$VOL_TESLA_USB" "$VOL_2TB" "recent_only" 2 0 0 1
  echo ""
  execute_safe_or_force_purge "$VOL_JOWUA" "$VOL_2TB" "capacity" 60 0 0 1

# JOWUA + 2TB Archive SSD
elif [[ -n "$VOL_JOWUA" && -n "$VOL_2TB" ]]; then
  echo "[Detected] JOWUA + 2TB Archive SSD detected."
  echo ""
  sync_volumes "$VOL_JOWUA" "$VOL_2TB" "JOWUA 1TB -> 2TB Archive"
  echo ""
  execute_safe_or_force_purge "$VOL_JOWUA" "$VOL_2TB" "capacity" 60 0 0 1

# Tesla USB + 2TB Archive SSD
elif [[ -n "$VOL_TESLA_USB" && -n "$VOL_2TB" ]]; then
  echo "[Detected] Tesla USB + 2TB Archive SSD detected."
  echo ""
  sync_volumes "$VOL_TESLA_USB" "$VOL_2TB" "Tesla USB -> 2TB Archive"
  echo ""
  execute_safe_or_force_purge "$VOL_TESLA_USB" "$VOL_2TB" "recent_only" 2 0 0 1

# JOWUA + Tesla USB (Archive Destination NOT present)
elif [[ -n "$VOL_JOWUA" && -n "$VOL_TESLA_USB" ]]; then
  echo "[Detected] Tesla USB + JOWUA detected (Master Archive is not connected)."
  echo ""
  sync_volumes "$VOL_TESLA_USB" "$VOL_JOWUA" "Tesla USB -> JOWUA 1TB"
  echo ""
  echo ">>> Purge Notice: Master Archive is not connected."
  echo "    Skipping all pruning to guarantee footage is ONLY deleted after being archived."
fi

echo ""
echo "======================================================"
echo "All operations completed successfully."
echo "======================================================"
