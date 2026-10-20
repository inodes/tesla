#!/bin/bash
# sync_jowua_rescue.sh - a copy of sync_dashcam.sh's copy leg (same rsync flags and excludes), fixed to JOWUA -> a new folder on LaCie1, with nothing else.
#
#   ./sync_jowua_rescue.sh [SOURCE [DEST]] [--dry-run] [--yes]
#
#   SOURCE   the mounted JOWUA volume (default "/Volumes/TESLADRIVE 1", the repaired copy mounted read-only by fix_image.sh)
#   DEST     the new folder (default /Volumes/LaCie1/jowua-rescue); it gets a TeslaCam/ folder inside, the same layout as the Archive
#   --dry-run  only work out how much would be copied and whether it fits
#   --yes      do not ask before starting
#   --since DATE  only clips and event folders dated DATE or later (2026-10-13): older ones are left out (use when they are already on the Archive and the space is short)
#
# Unlike sync_dashcam.sh this never deletes anything (no purge, no "make room", no tombstones, nothing is removed from SOURCE or from DEST) and it does not touch any other drive.
# It stops before copying if the space is short. Resumable: run it again and only what is missing is copied. When it ends it checks that DEST is up to date and compares counts.
# Everything is logged with the time to ~/Downloads/sync-jowua-rescue-DATE.log (rsync's own output goes to the same file after the run). Give it to Claude.
set -u -o pipefail
say() { echo "$(date '+%H:%M:%S') • $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

SRC="" DST="" DRY=0 YES=0 SINCE=""
while [ $# -gt 0 ]; do a="$1"; shift; case "$a" in
  --dry-run|-n) DRY=1 ;; --yes|-y) YES=1 ;; --since) SINCE="${1:-}"; shift ;; -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  -*) die "unknown option: $a" ;; *) if [ -z "$SRC" ]; then SRC="$a"; elif [ -z "$DST" ]; then DST="$a"; else die "unexpected argument: $a"; fi ;; esac; done
[ -z "$SINCE" ] || [[ "$SINCE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "--since needs a date like 2026-10-13"
SRC="${SRC:-/Volumes/TESLADRIVE 1}"; DST="${DST:-/Volumes/LaCie1/jowua-rescue}"; SRC="${SRC%/}"; DST="${DST%/}"
[ -d "$SRC/TeslaCam" ] || die "no TeslaCam folder in '$SRC' (is the repaired copy mounted? run fix_image.sh first)"
case "$DST" in "$SRC"|"$SRC"/*) die "the destination is inside the source" ;; esac

RSYNC_BIN=/usr/bin/rsync
for r in /opt/homebrew/bin/rsync /usr/local/bin/rsync; do [ -x "$r" ] && { RSYNC_BIN="$r"; break; }; done
LOGDIR="$HOME/Downloads"; [ -d "$LOGDIR" ] && [ -w "$LOGDIR" ] || LOGDIR=/tmp
STAMP=$(date +%Y%m%d-%H%M%S); LOG="$LOGDIR/sync-jowua-rescue-$STAMP.log"; RLOG="$LOGDIR/sync-jowua-rescue-$STAMP.rsync.log"
EXF=""
if [ -n "$SINCE" ]; then   # leave out everything dated before SINCE: top-level names in RecentClips / SavedClips / SentryClips start with the date
  EXF=$(mktemp /tmp/jowua-exclude.XXXXXX)
  for k in RecentClips SavedClips SentryClips; do
    [ -d "$SRC/TeslaCam/$k" ] || continue
    ls "$SRC/TeslaCam/$k" | cut -c1-10 | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort -u | while read -r d; do [[ "$d" < "$SINCE" ]] && echo "/$k/$d*"; done
  done > "$EXF"
fi
OPTS=(-a --modify-window=2 --exclude=.Spotlight-V100 --exclude=.Trashes "--exclude=System Volume Information" --exclude=.fseventsd "--exclude=.*" "--exclude=._*" --exclude=Tools --exclude=Icons --exclude=TeslaCam_Exports --exclude=TeslaCam_Archive)
[ -z "$EXF" ] || OPTS+=("--exclude-from=$EXF")

main() {
[ -z "$SINCE" ] || say "only clips dated $SINCE or later ($(wc -l < "$EXF" | tr -d ' ') day patterns left out)"
say "sync_jowua_rescue: $SRC/TeslaCam/  ->  $DST/TeslaCam/   (rsync: $RSYNC_BIN $("$RSYNC_BIN" --version 2>&1 | head -1 | awk '{print $3}'), dry-run=$DRY)"
CHK="$DST"; while [ ! -d "$CHK" ]; do CHK=$(dirname "$CHK"); done
say "Calculating what has to be copied (reading the folder listings of the source)..."
OUT=$("$RSYNC_BIN" "${OPTS[@]}" --dry-run --stats "$SRC/TeslaCam/" "$DST/TeslaCam/" 2>&1); RC=$?
echo "$OUT" | grep -E "^rsync|IO error|Number of|Total (file|transferred)" | head -20
[ "$RC" -eq 0 ] || [ "$RC" -eq 23 ] || [ "$RC" -eq 24 ] || die "rsync could not even list the work (status $RC)"
BYTES=$(echo "$OUT" | awk '/Total transferred file size:/ {print $5}' | tr -d ','); BYTES=${BYTES:-0}
FILES=$(echo "$OUT" | awk '/Number of (regular )?files transferred:/ {print $NF}' | tr -d ','); FILES=${FILES:-?}
FREE_KB=$(df -k "$CHK" | awk 'NR==2 {print $4}')
say "To copy: $FILES files, $((BYTES / 1000000000)) GB ($BYTES bytes). Free at the destination ($CHK): $((FREE_KB / 976563)) GB"
if [ "$BYTES" -eq 0 ]; then say "Nothing to copy: the destination is already up to date."; fi
if [ $((BYTES / 1024 + 1048576)) -gt "$FREE_KB" ]; then die "not enough room: $((BYTES / 1000000000)) GB to copy plus 1 GB spare, $((FREE_KB / 976563)) GB free. Nothing was copied. Free some space on $CHK (for example delete the raw image once this footage is safe) and run again"; fi
[ "$DRY" -eq 1 ] && { say "dry run: nothing copied. Run again without --dry-run to copy."; return 0; }
[ "$BYTES" -eq 0 ] && return 0
if [ "$YES" -ne 1 ]; then printf "Copy %s GB now? [y/N]: " "$((BYTES / 1000000000))"; read -r ans; case "$ans" in y|Y|yes|YES) ;; *) die "not confirmed" ;; esac; fi

mkdir -p "$DST/TeslaCam" || die "cannot create $DST/TeslaCam"
find "$DST/TeslaCam" -maxdepth 2 -type f -name '.20??-??-??_*.mp4.??????' -delete 2>/dev/null   # rsync temp files from an interrupted run (same as sync_dashcam.sh)
say "Copying... (Ctrl-C stops it; run again to resume)"
caffeinate -i -w $$ 2>/dev/null &
START=$(date +%s); BASE_KB=$(df -k "$CHK" | awk 'NR==2 {print $3}')
"$RSYNC_BIN" "${OPTS[@]}" --stats "$SRC/TeslaCam/" "$DST/TeslaCam/" >"$RLOG" 2>&1 &
RP=$!; trap 'kill "$RP" 2>/dev/null; say "Stopped. Run again to resume."; exit 130' INT TERM
while kill -0 "$RP" 2>/dev/null; do
  sleep 30
  CUR=$(df -k "$CHK" | awk 'NR==2 {print $3}'); DONE=$(( (CUR - BASE_KB) * 1024 )); [ "$DONE" -lt 0 ] && DONE=0
  EL=$(( $(date +%s) - START )); [ "$EL" -lt 1 ] && EL=1; RATE=$(( DONE / EL / 1000000 ))
  PCT=$(( DONE * 100 / (BYTES + 1) )); [ "$PCT" -gt 99 ] && PCT=99
  if [ "$RATE" -gt 0 ]; then LEFT=$(( (BYTES - DONE) / 1000000 / RATE / 60 )); else LEFT="?"; fi
  say "  ${PCT}%  $((DONE / 1000000000)) GB of $((BYTES / 1000000000)) GB  ${RATE} MB/s  ${LEFT} min left"
done
wait "$RP"; XC=$?; trap - INT TERM
say "rsync finished with status $XC after $(( ($(date +%s) - START) / 60 )) min"
grep -E "^rsync|IO error|rsync error|failed:|No such file|Input/output" "$RLOG" | sort | uniq -c | sort -rn | head -15
grep -E "Number of (regular )?files transferred|Total transferred file size" "$RLOG"

# ---- check: is the destination now up to date? and do the counts agree?
say "Checking the result (a second listing pass; nothing is copied)..."
LEFT_OUT=$("$RSYNC_BIN" "${OPTS[@]}" --dry-run --itemize-changes "$SRC/TeslaCam/" "$DST/TeslaCam/" 2>&1 | grep -E '^[<>c]f' | head -20); 
if [ -z "$LEFT_OUT" ]; then say "CHECK OK: nothing left to copy."; else say "WARNING: still to copy (first lines):"; echo "$LEFT_OUT"; fi
S=$(find "$SRC/TeslaCam" -type f -name '*.mp4' 2>/dev/null | wc -l | tr -d ' '); D=$(find "$DST/TeslaCam" -type f -name '*.mp4' 2>/dev/null | wc -l | tr -d ' ')
say "mp4 files: source $S, destination $D"
if [ "$XC" -eq 0 ] && [ -z "$LEFT_OUT" ]; then say "RESULT: copied and checked."; return 0; fi
say "RESULT: NOT complete (rsync status $XC). Files that could not be read are in $RLOG. Run again to retry them."; return 2
}

main 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
[ -f "$RLOG" ] && { echo "---- rsync output ----"; cat "$RLOG"; } >>"$LOG"
echo "Log: $LOG"
exit "$RC"
