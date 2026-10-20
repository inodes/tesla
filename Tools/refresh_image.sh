#!/bin/bash
# refresh_image.sh - overwrite the raw image with a fresh, UNREPAIRED copy of the disk, then attach it read-only and mount it read-only. Nothing is repaired; the disk is only read.
#
#   ./refresh_image.sh DISK IMAGE            e.g.  ./refresh_image.sh disk2 /Volumes/LaCie1/jowua-raw.img
#
# Why: fix_image.sh's repair deleted the damaged directory entries from the image, and with them the clips from the listing. The disk still has them. This puts the original
# back into the same image file (no extra space needed), mounted read-only exactly as the disk was, so sync_jowua_rescue.sh can copy what the repaired copy lost.
# Steps: 1 detach any attached copy of IMAGE | 2 unmount DISK | 3 copy DISK over IMAGE (dd, bs=1m, bad blocks zero-filled and counted) + re-read the one known bad stretch in 64 KB pieces |
#        4 check the size | 5 attach read-only, mount read-only, count the files and time a listing.
# Log: ~/Downloads/refresh-image-DATE.log (give it to Claude). Exit status 0 = done, 1 = failed (the old image is then partly overwritten: run it again).
set -u -o pipefail
die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "$(date '+%H:%M:%S') • $*"; }
[ "$(uname)" = Darwin ] || die "this is for macOS"
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
DISK="${1:-}" IMG="${2:-}"; [ -n "$DISK" ] && [ -n "$IMG" ] || { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
DISK=${DISK#/dev/}; case "$DISK" in rdisk*) DISK=${DISK#r} ;; esac
[[ "$DISK" =~ ^disk[0-9]+$ ]] || die "'$DISK' is not a whole disk like disk2"
[ -f "$IMG" ] || die "no image file at $IMG (this refreshes an existing image)"
HOME_DIR=$(eval echo "~${SUDO_USER:-$USER}"); LOGDIR="$HOME_DIR/Downloads"; [ -d "$LOGDIR" ] && [ -w "$LOGDIR" ] || LOGDIR=/tmp
LOG="$LOGDIR/refresh-image-$(date +%Y%m%d-%H%M%S).log"; DDLOG="$LOGDIR/refresh-image-dd-$(date +%Y%m%d-%H%M%S).log"
SIZE=$(diskutil info "$DISK" | sed -n 's/.*Disk Size:.*(\([0-9]*\) Bytes).*/\1/p' | head -1)
[[ "$SIZE" =~ ^[0-9]+$ ]] || die "cannot read the size of $DISK"
capture() { local secs=$1; shift; local f; f=$(mktemp /tmp/refresh.XXXXXX); "$@" >"$f" 2>&1 & local p=$! i=0 rc
  while kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i + 1)); if [ "$i" -ge "$secs" ]; then kill -9 "$p" 2>/dev/null; cat "$f"; echo "(gave up after ${secs}s)"; rm -f "$f"; return 124; fi; done
  wait "$p"; rc=$?; cat "$f"; rm -f "$f"; return "$rc"; }
limited() { local secs=$1; shift; "$@" >/dev/null 2>&1 & local p=$! i=0
  while kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i + 1)); if [ "$i" -ge "$secs" ]; then kill -9 "$p" 2>/dev/null; return 124; fi; done; wait "$p"; }

run() {
echo "=== refresh_image.sh $(date '+%Y-%m-%d %H:%M:%S') disk=$DISK ($SIZE bytes) image=$IMG  macOS $(sw_vers -productVersion)"
say "Step 1/5: detach any attached copy of the image"
for dev in $(hdiutil info | awk -v img="$IMG" '/^image-path/ {p=($0 ~ img)} p && /^\/dev\/disk[0-9]+[ \t]/ {print $1}' | sort -u); do
  say "  detaching $dev"; for m in $(diskutil list "$dev" 2>/dev/null | awk '/disk[0-9]+s[0-9]+$/ {print $NF}'); do diskutil unmount force "$m" >/dev/null 2>&1; done
  hdiutil detach "$dev" -force 2>&1 || die "could not detach $dev"
done
if hdiutil info | grep -q "$IMG"; then die "the image is still attached (see: hdiutil info). Eject its volume in Finder and run again"; fi

say "Step 2/5: unmount $DISK (nothing may have it open; quit Claude if this is refused)"
OUT=$(capture 60 diskutil unmountDisk "$DISK") || die "$DISK could not be unmounted:
$OUT"
say "  $(echo "$OUT" | head -1)"

say "Step 3/5: copy $DISK over $IMG (about 35 min)"
: > "$DDLOG"; T0=$(date +%s)
dd if="/dev/r$DISK" of="$IMG" bs=1m conv=noerror,sync,notrunc 2>>"$DDLOG" &
DP=$!; caffeinate -i -w "$DP" 2>/dev/null &
trap 'kill "$DP" 2>/dev/null; say "Stopped. The image is partly overwritten: run this again."; exit 1' INT TERM
while kill -0 "$DP" 2>/dev/null; do
  sleep 60; kill -INFO "$DP" 2>/dev/null; sleep 1
  L=$(grep 'bytes transferred' "$DDLOG" | tail -1 | awk '{print $1}'); L=${L:-0}
  EL=$(( $(date +%s) - T0 )); [ "$EL" -lt 1 ] && EL=1; R=$(( L / EL / 1000000 ))
  say "  $(( L * 100 / SIZE ))%  $(( L / 1000000000 )) GB  ${R} MB/s  $(grep -c '^dd:' "$DDLOG") read error(s) so far"
done
wait "$DP"; RC=$?; trap - INT TERM
ERRS=$(grep -c '^dd:' "$DDLOG"); say "dd finished with status $RC after $(( ($(date +%s) - T0) / 60 )) min; $ERRS read error line(s)"
[ "$ERRS" -gt 0 ] && { grep '^dd:' "$DDLOG" | sort | uniq -c | head -5; }
[ "$RC" -eq 0 ] || die "dd did not finish (status $RC)"
say "  re-reading the stretch around 96 GiB that failed before, in 64 KB pieces..."
dd if="/dev/r$DISK" of="$IMG" bs=64k skip=1569920 seek=1569920 count=8960 conv=noerror,sync,notrunc 2>&1 | tail -3

say "Step 4/5: check the size"
CUR=$(stat -f %z "$IMG"); say "  image $CUR bytes, disk $SIZE bytes"
if [ "$CUR" -lt "$SIZE" ]; then
  say "  the end is missing ($((SIZE - CUR)) bytes): adding it"
  dd if="/dev/r$DISK" of="$IMG" bs=1m skip=$(( CUR / 1048576 )) seek=$(( CUR / 1048576 )) conv=notrunc 2>&1 | tail -2
  CUR=$(stat -f %z "$IMG")
fi
[ "$CUR" -eq "$SIZE" ] || die "the image is $CUR bytes, the disk $SIZE: not complete"

say "Step 5/5: attach read-only and mount read-only (no repair)"
OUT=$(hdiutil attach -readonly -imagekey diskimage-class=CRawDiskImage -nomount -noverify "$IMG" 2>&1) || die "could not attach: $OUT"
echo "$OUT"; WHOLE=$(echo "$OUT" | awk 'NR==1{print $1}'); PART=$(echo "$OUT" | awk '$1 ~ /s[0-9]+$/ {p=$1} END{print p}')
[ -n "$PART" ] || die "no partition found in the attached image ($WHOLE)"
diskutil unmount "$PART" >/dev/null 2>&1
diskutil mount readOnly "$PART" 2>&1 || die "could not mount $PART read-only (still attached as $WHOLE)"
MP=$(diskutil info "$PART" | sed -n 's|^ *Mount Point: *||p' | head -1); say "mounted read-only at: ${MP:-?}"
T1=$(date +%s)
if limited 120 bash -c "ls '$MP/TeslaCam/RecentClips' | wc -l > /tmp/refresh.count"; then
  say "RecentClips: $(cat /tmp/refresh.count) entries (listed in $(( $(date +%s) - T1 )) s)"
else say "WARNING: listing RecentClips took over 120 s"; fi
for d in SavedClips SentryClips; do [ -d "$MP/TeslaCam/$d" ] && say "$d: $(ls "$MP/TeslaCam/$d" | wc -l | tr -d ' ') entries"; done
say "RESULT: fresh unrepaired copy mounted read-only at $MP (image attached as $WHOLE). Next: ./sync_jowua_rescue.sh --dry-run"
}
run 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}; echo "Log: $LOG"; exit "$RC"
