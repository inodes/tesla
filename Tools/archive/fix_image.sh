#!/bin/bash
# fix_image.sh - repair the file system INSIDE a raw disk image (made by image_disk.sh), check it, mount it read-only. The real disk is never touched.
#
#   ./fix_image.sh IMAGE [--probe FILE] [--no-repair]
#
#   IMAGE        the raw image: /Volumes/LaCie1/jowua-raw.img
#   --probe FILE after the repair, read this file from the mounted image and show the speed (path inside the disk, e.g. TeslaCam/RecentClips/2026-10-16_15-02-00-front.mp4)
#   --no-repair  only attach, check (fsck_exfat -n) and mount read-only
#
# Steps: 1 attach the image as a disk (no mount) | 2 check (fsck_exfat -n) | 3 repair (fsck_exfat -y, on the image only) | 4 check again | 5 mount READ-ONLY, list, probe.
# Everything is logged with the time to ~/Downloads/fix-image-DATE.log. When done, detach with:  hdiutil detach <the disk shown>
# Exit status: 0 = clean and mounted, 3 = still not clean (mounted read-only anyway), 1 = could not attach or mount.

set -u -o pipefail
die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "$(date '+%H:%M:%S') • $*"; }
[ "$(uname)" = Darwin ] || die "this is for macOS"
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

IMG="" PROBE="" REPAIR=1
while [ $# -gt 0 ]; do
  case "$1" in --probe) PROBE="${2:-}"; shift ;; --no-repair) REPAIR=0 ;; -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; -*) die "unknown option: $1" ;; *) [ -z "$IMG" ] && IMG="$1" || die "unexpected argument: $1" ;; esac
  shift
done
[ -f "$IMG" ] || die "no such image: $IMG"
HOME_DIR=$(eval echo "~${SUDO_USER:-$USER}"); LOGDIR="$HOME_DIR/Downloads"; [ -d "$LOGDIR" ] && [ -w "$LOGDIR" ] || LOGDIR=/tmp
LOG="$LOGDIR/fix-image-$(date +%Y%m%d-%H%M%S).log"

limited() { local secs=$1; shift; "$@" >/dev/null 2>&1 & local p=$! i=0
  while kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i + 1)); if [ "$i" -ge "$secs" ]; then kill -9 "$p" 2>/dev/null; return 124; fi; done; wait "$p"; }

run() {
echo "=== fix_image.sh $(date '+%Y-%m-%d %H:%M:%S')  image=$IMG ($(stat -f %z "$IMG") bytes) probe=${PROBE:-none} repair=$REPAIR  macOS $(sw_vers -productVersion)"

say "Step 1/5: attach the image (no mount)"
OUT=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount -noverify "$IMG" 2>&1) || die "could not attach the image:
$OUT"
echo "$OUT"
WHOLE=$(echo "$OUT" | awk 'NR==1{print $1}'); PART=$(echo "$OUT" | awk '$1 ~ /s[0-9]+$/ {p=$1} END{print p}')
[ -n "$PART" ] || die "the image attached as $WHOLE but no partition was found (output above)"
RAW=${PART/\/dev\//\/dev\/r}
say "attached: whole disk $WHOLE, file system on $PART"
diskutil unmount "$PART" >/dev/null 2>&1   # if macOS tried to mount it read-write

say "Step 2/5: check the file system (fsck_exfat -n, nothing changed)"
fsck_exfat -n "$RAW" 2>&1; C1=$?
say "check 1 exit status: $C1"

if [ "$REPAIR" -eq 1 ] && [ "$C1" -ne 0 ]; then
  say "Step 3/5: repair (fsck_exfat -y) - on the image only; the real disk is not touched"
  fsck_exfat -y "$RAW" 2>&1; say "repair exit status: $?"
  say "Step 4/5: check again (fsck_exfat -n)"
  fsck_exfat -n "$RAW" 2>&1; C2=$?; say "check 2 exit status: $C2"
else
  say "Steps 3-4: ${REPAIR:+skipped: }$([ "$C1" -eq 0 ] && echo "already clean" || echo "--no-repair")"; C2=$C1
fi

say "Step 5/5: mount read-only and test"
diskutil mount readOnly "$PART" 2>&1 || die "could not mount $PART read-only (the image stays attached as $WHOLE)"
MP=$(diskutil info "$PART" | sed -n 's|^ *Mount Point: *||p' | head -1)
say "mounted read-only at: ${MP:-?}"
if limited 30 ls "$MP"; then say "listing of the top folder answered"; else say "WARNING: listing the top folder took over 30 s or failed"; fi
for d in RecentClips SavedClips SentryClips; do
  [ -d "$MP/TeslaCam/$d" ] && { N=$(ls "$MP/TeslaCam/$d" 2>/dev/null | wc -l | tr -d ' '); say "TeslaCam/$d: $N entries"; }
done
if [ -n "$PROBE" ]; then
  F="$MP/${PROBE#/}"
  if [ ! -f "$F" ]; then say "WARNING: probe file not found: $F"
  else T0=$(date +%s); if limited 60 dd if="$F" of=/dev/null bs=4m; then say "probe read OK: $(( $(stat -f %z "$F") / 1048576 )) MB in $(( $(date +%s) - T0 + 1 )) s ($F)"; else say "WARNING: reading the probe file took over 60 s or failed: $F"; fi; fi
fi
if [ "$C2" -eq 0 ]; then say "RESULT: file system clean; mounted read-only at $MP. Detach later with: hdiutil detach $WHOLE"
else say "RESULT: file system NOT clean (status $C2); mounted read-only at $MP. Detach later with: hdiutil detach $WHOLE"; fi
echo "C2=$C2" >/dev/null
return "$C2"
}

run 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
echo "Log: $LOG"
[ "$RC" -eq 0 ] || [ "$RC" -eq 1 ] || RC=3
exit "$RC"
