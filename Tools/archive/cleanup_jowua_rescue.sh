#!/bin/bash
# cleanup_jowua_rescue.sh - after the JOWUA footage is rescued: detach the raw image and delete it (and its logs) to free about 950 GB.
#
#   ./cleanup_jowua_rescue.sh [IMAGE [RESCUE_DIR]] [--yes]
#
#   IMAGE       the raw image (default /Volumes/LaCie1/jowua-raw.img)
#   RESCUE_DIR  the rescued footage (default /Volumes/LaCie1/jowua-rescue); it must have a TeslaCam folder with at least 10000 mp4 files, or nothing is deleted
#   --yes       do not ask you to type "delete" first
#
# Steps: 1 check the rescued copy is there | 2 detach the image (unmounts the read-only copy of JOWUA) | 3 delete IMAGE, IMAGE.log and IMAGE.dd.log (permanent, no Trash).
# It never touches the JOWUA disk itself, the rescue folder, or anything else. Log: ~/Downloads/cleanup-jowua-rescue-DATE.log
set -u -o pipefail
say() { echo "$(date '+%H:%M:%S') • $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
IMG="" RES="" YES=0
for a in "$@"; do case "$a" in --yes|-y) YES=1 ;; -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; -*) die "unknown option: $a" ;; *) if [ -z "$IMG" ]; then IMG="$a"; elif [ -z "$RES" ]; then RES="$a"; else die "unexpected argument: $a"; fi ;; esac; done
IMG="${IMG:-/Volumes/LaCie1/jowua-raw.img}"; RES="${RES:-/Volumes/LaCie1/jowua-rescue}"
LOGDIR="$HOME/Downloads"; [ -d "$LOGDIR" ] && [ -w "$LOGDIR" ] || LOGDIR=/tmp
LOG="$LOGDIR/cleanup-jowua-rescue-$(date +%Y%m%d-%H%M%S).log"
main() {
say "cleanup_jowua_rescue: image=$IMG rescue=$RES"
say "Step 1/3: check the rescued copy"
[ -d "$RES/TeslaCam" ] || die "no $RES/TeslaCam: nothing deleted"
N=$(find "$RES/TeslaCam" -type f -name '*.mp4' 2>/dev/null | wc -l | tr -d ' ')
say "  $N mp4 files in the rescue folder"
[ "$N" -ge 10000 ] || die "only $N mp4 files in $RES/TeslaCam (expected at least 10000): nothing deleted"
[ -f "$IMG" ] || say "  note: no image file at $IMG (already deleted?)"
say "Step 2/3: detach the image"
for dev in $(hdiutil info | awk -v img="$IMG" '/^image-path/ {p=(index($0, img) > 0)} p && /^\/dev\/disk[0-9]+[ \t]/ {print $1}' | sort -u); do
  say "  detaching $dev"
  for m in $(diskutil list "$dev" 2>/dev/null | awk '/disk[0-9]+s[0-9]+$/ {print $NF}'); do diskutil unmount "$m" >/dev/null 2>&1; done
  hdiutil detach "$dev" 2>&1 || die "could not detach $dev (close Finder windows on it, then run again): nothing deleted"
done
hdiutil info | grep -q "$IMG" && die "the image is still attached: nothing deleted"
say "  image detached"
if [ ! -e "$IMG" ] && [ ! -e "$IMG.log" ] && [ ! -e "$IMG.dd.log" ]; then say "nothing to delete"; return 0; fi
SZ=$(ls -l "$IMG" 2>/dev/null | awk '{print $5}')
say "Step 3/3: delete $IMG (${SZ:-0} bytes), $IMG.log and $IMG.dd.log. This is permanent."
if [ "$YES" -ne 1 ]; then printf 'Type "delete" to go ahead: '; read -r ans; [ "$ans" = delete ] || die "not confirmed: nothing deleted"; fi
for f in "$IMG" "$IMG.log" "$IMG.dd.log"; do [ -e "$f" ] && { rm -f "$f" && say "  deleted $f" || say "  FAILED to delete $f"; }; done
say "free space on $(dirname "$IMG"): $(df -h "$(dirname "$IMG")" | awk 'NR==2 {print $4}')"
say "RESULT: done. The JOWUA disk itself was not touched; the rescued footage is in $RES"
}
main 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}; echo "Log: $LOG"; exit "$RC"
