#!/bin/bash
# fix_disk.sh - fix a damaged external disk in one go: wait until nothing holds it (Claude must be quit), image it, repair its file system, check it, mount it read-only.
#
#   ./fix_disk.sh DISK IMAGE [--probe FILE] [--wait MINUTES] [--skip-image] [--repair-anyway]
#
#   DISK            the whole disk, as in `diskutil list`: disk2          (find it: mount | grep "TESLADRIVE 1")
#   IMAGE           where the raw image goes, on ANOTHER disk with room:  /Volumes/LaCie1/jowua-raw.img
#   --probe FILE    after the repair, read this file from the disk and show the speed (e.g. the clip that stalled)
#   --wait MINUTES  how long to wait for the disk to be released (default 20)
#   --skip-image    repair without making an image first (NOT recommended: the repair cannot be undone)
#   --repair-anyway repair even when the image had read errors or a sample differed
#
# Steps:  1 wait for the disk to be released: tries to unmount it every 5 s and says what is holding it (Quit Claude: Cmd-Q; its virtual machine holds a
#           connected folder) | 2 image it (image_disk.sh: checks, copy, samples compared) | 3 repair the file system (diskutil repairVolume) |
#           4 check it again (diskutil verifyVolume) | 5 mount it READ-ONLY and test it: a listing and the --probe file, each with a time limit.
# It stops at the first step that fails and says so. Nothing is ever forced or deleted.
# Everything is logged, with the time, to ~/Downloads/fix-disk-DISK-DATE.log. When Claude is open again, give it that file.

set -u -o pipefail
die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "$(date '+%H:%M:%S') • $*"; }
usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }

[ "$(uname)" = Darwin ] || die "this is for macOS (it uses diskutil)"
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

DISK="" IMG="" PROBE="" WAIT=20 SKIPIMG=0 ANYWAY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE="${2:-}"; shift ;; --wait) WAIT="${2:-}"; shift ;; --skip-image) SKIPIMG=1 ;; --repair-anyway) ANYWAY=1 ;; -h|--help) usage 0 ;;
    -*) die "unknown option: $1" ;;
    *) if [ -z "$DISK" ]; then DISK="$1"; elif [ -z "$IMG" ]; then IMG="$1"; else die "unexpected argument: $1"; fi ;;
  esac
  shift
done
[ -n "$DISK" ] || usage 1
DISK=${DISK#/dev/}; case "$DISK" in rdisk*) DISK=${DISK#r} ;; esac
[[ "$DISK" =~ ^disk[0-9]+$ ]] || die "'$DISK' is not a whole disk like disk2"
[ -n "$IMG" ] || [ "$SKIPIMG" -eq 1 ] || usage 1
[[ "$WAIT" =~ ^[0-9]+$ ]] || die "--wait needs a number of minutes"
HERE=$(cd "$(dirname "$0")" && pwd)
HOME_DIR=$(eval echo "~${SUDO_USER:-$USER}"); LOGDIR="$HOME_DIR/Downloads"; [ -d "$LOGDIR" ] && [ -w "$LOGDIR" ] || LOGDIR=/tmp
LOG="$LOGDIR/fix-disk-$DISK-$(date +%Y%m%d-%H%M%S).log"
PART="${DISK}s1"

limited() { local secs=$1; shift; "$@" >/dev/null 2>&1 & local p=$! i=0
  while kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i + 1)); if [ "$i" -ge "$secs" ]; then kill -9 "$p" 2>/dev/null; return 124; fi; done; wait "$p"; }

# capture SECS CMD...: run CMD, print its output; give up after SECS (status 124) so a stalled disk cannot hang this script
capture() { local secs=$1; shift; local f; f=$(mktemp /tmp/fixdisk.XXXXXX); "$@" >"$f" 2>&1 & local p=$! i=0 rc
  while kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i + 1)); if [ "$i" -ge "$secs" ]; then kill -9 "$p" 2>/dev/null; cat "$f"; echo "(gave up after ${secs}s: the disk does not answer)"; rm -f "$f"; return 124; fi; done
  wait "$p"; rc=$?; cat "$f"; rm -f "$f"; return "$rc"; }

run() {
echo "=== fix_disk.sh $(date '+%Y-%m-%d %H:%M:%S')  disk=$DISK image=${IMG:-none} probe=${PROBE:-none} wait=${WAIT}min  macOS $(sw_vers -productVersion)"
echo "--- diskutil info $DISK ---"; diskutil info "$DISK" 2>&1 | grep -E "Device Node|Device / Media Name|Disk Size|Protocol|Location|Whole|SMART"; echo "---"
echo "--- diskutil list $DISK ---"; diskutil list "$DISK" 2>&1; echo "---"
[ "$SKIPIMG" -eq 1 ] && say "WARNING: --skip-image: no image will be made first"

# ---- 1. wait until the disk can be unmounted (nothing has it open)
say "Step 1/5: waiting for $DISK to be released (up to $WAIT min). If this does not move on, quit the Claude app (Cmd-Q) or any app showing this disk."
END=$(( $(date +%s) + WAIT * 60 )); N=0
while :; do
  N=$((N + 1))
  CL=$(pgrep -f '/Claude.app/Contents/MacOS/Claude' 2>/dev/null | tr '\n' ' ')
  if OUT=$(capture 60 diskutil unmountDisk "$DISK"); then say "Released: $(echo "$OUT" | head -1)"; break; fi
  WHO=$(echo "$OUT" | grep -o 'PID [0-9]* ([^)]*)' | head -2 | tr '\n' ' ')
  if [ -n "$CL" ]; then CLS="yes (pid ${CL% })"; else CLS="no"; fi
  say "attempt $N: not released. Claude running: $CLS. Held by: ${WHO:-(not named; $(echo "$OUT" | head -1))}"
  [ "$(date +%s)" -lt "$END" ] || die "still held after $WAIT min. Quit Claude (Cmd-Q) and anything showing the disk, then run this again"
  sleep 5
done

# ---- 2. image
if [ "$SKIPIMG" -eq 1 ]; then say "Step 2/5: skipped (no image)"; else
  say "Step 2/5: image -> $IMG"
  "$HERE/image_disk.sh" "$DISK" "$IMG" --yes; RC=$?
  case "$RC" in
    0) say "image complete" ;;
    2) [ "$ANYWAY" -eq 1 ] || die "the image finished with warnings (read errors or a sample that differs; see above). Not repairing. Look at the log, or use --repair-anyway" ;;
    *) die "the image was not made (status $RC; see above). Nothing was repaired" ;;
  esac
fi

# ---- 3. repair
say "Step 3/5: repair the file system ($PART)"
diskutil unmountDisk "$DISK" >/dev/null 2>&1
diskutil repairVolume "$PART" 2>&1; RC=$?
REPRC=$RC
[ "$RC" -eq 0 ] && say "repairVolume finished OK" || say "WARNING: repairVolume exited with status $RC (see above)"

# ---- 4. check
say "Step 4/5: check it again (read-only check)"
diskutil verifyVolume "$PART" 2>&1; VRC=$?
[ "$VRC" -eq 0 ] && say "verifyVolume: the file system is clean" || say "WARNING: verifyVolume exited with status $VRC: it is still not clean (see above)"

# ---- 5. mount read-only and test
say "Step 5/5: mount read-only and test"
diskutil unmount "$PART" >/dev/null 2>&1
if ! diskutil mount readOnly "$PART" 2>&1; then die "could not mount $PART read-only"; fi
MP=$(diskutil info "$PART" | sed -n 's|^ *Mount Point: *||p' | head -1)
say "mounted read-only at: ${MP:-?}"
if limited 20 ls "$MP"; then say "listing of the top folder answered"; else say "WARNING: listing the top folder took over 20 s or failed"; fi
if [ -d "$MP/TeslaCam" ]; then if limited 30 ls "$MP/TeslaCam/RecentClips"; then say "listing of TeslaCam/RecentClips answered"; else say "WARNING: listing TeslaCam/RecentClips took over 30 s or failed"; fi; fi
if [ -n "$PROBE" ]; then
  F="$MP/${PROBE#/}"; [ -e "$PROBE" ] && F="$PROBE"
  if [ ! -f "$F" ]; then say "WARNING: probe file not found: $F"
  else T0=$(date +%s); if limited 60 dd if="$F" of=/dev/null bs=4m; then T1=$(date +%s); B=$(stat -f %z "$F"); say "probe read OK: $((B / 1048576)) MB in $((T1 - T0 + 1)) s ($F)"; else say "WARNING: reading the probe file took over 60 s or failed: $F"; fi; fi
fi
if [ "$VRC" -eq 0 ]; then say "RESULT: repair status $REPRC, check clean; mounted read-only at $MP. Log: $LOG"; else say "RESULT: repair status $REPRC, but the check is still NOT clean (status $VRC); mounted read-only at $MP. Log: $LOG"; fi
[ "$VRC" -eq 0 ] || exit 3
}

run 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
echo "Log: $LOG"
exit "$RC"
