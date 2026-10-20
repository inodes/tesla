#!/bin/bash
# reformat_drive.sh - wipe a damaged TeslaCam drive, make it a fresh exFAT drive, and test the whole surface (write, read back, compare).
#
# NAME
#     reformat_drive.sh - erase a TeslaCam drive as exFAT, check the surface, leave it ready for the car
#
# SYNOPSIS
#     reformat_drive.sh DISK [--label NAME] [--marker NAME] [--test GB|all|none] [--yes] [--no-inventory-check]
#
# DESCRIPTION
#     DISK is the whole disk as macOS names it (diskutil list), for example disk2. Everything on it is ERASED. The script refuses unless the newest
#     jowua-inventory log in ~/Downloads (inventory_jowua.py) says every footage file has a verified copy elsewhere and is less than 3 days old.
#     It then asks you to type the disk name, unmounts it, erases it (exFAT, MBR, as the car expects), creates TeslaCam/ and the marker folder, and
#     tests the surface: it fills the disk with 1 GiB files of one known pattern, reads each back and compares it, and reports errors and slow files.
#     The test files are deleted at the end. A new volume has a NEW volume UUID: register it again in the app.
#
# OPTIONS
#     --label NAME          volume name (default JOWUA)
#     --marker NAME         empty folder created at the top (default JOWUA_1TB); none = no folder
#     --test GB|all|none    how much to test (default all: about 1.5 hours for 1 TB)
#     --yes                 do not ask for the typed confirmation (the inventory check still applies)
#     --no-inventory-check  skip the inventory check (do not use it unless the footage is safe)
#
# EXIT STATUS
#     0 formatted and the surface test passed   1 refused or failed before erasing   2 erased, but the surface test found problems
#
# FILES
#     ~/Downloads/reformat-drive-DATE.log   the log (give it to Claude)
set -u -o pipefail
say() { echo "$(date '+%H:%M:%S') • $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
usage() { sed -n '4,31p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }
case "${1:-}" in -h|--help) usage 0 ;; esac
[ "$(uname)" = Darwin ] || die "this is for macOS"
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

DISK="" LABEL=JOWUA MARKER=JOWUA_1TB TEST=all YES=0 NOINV=0
while [ $# -gt 0 ]; do a="$1"; shift; case "$a" in
  --label) LABEL="${1:-}"; shift ;; --marker) MARKER="${1:-}"; shift ;; --test) TEST="${1:-}"; shift ;; --yes|-y) YES=1 ;; --no-inventory-check) NOINV=1 ;;
  -h|--help) usage 0 ;; -*) die "unknown option: $a" ;; *) [ -z "$DISK" ] && DISK="$a" || die "unexpected argument: $a" ;; esac; done
[ -n "$DISK" ] || usage 1
DISK=${DISK#/dev/}; case "$DISK" in rdisk*) DISK=${DISK#r} ;; esac
[[ "$DISK" =~ ^disk[0-9]+$ ]] || die "'$DISK' is not a whole disk like disk2"
[[ "$LABEL" =~ ^[A-Za-z0-9_\ -]{1,11}$ ]] || die "--label must be 1 to 11 letters, digits, space, - or _"
[[ "$TEST" =~ ^([0-9]+|all|none)$ ]] || die "--test needs a number of GB, all or none"
HOME_DIR=$(eval echo "~${SUDO_USER:-$USER}"); LOGDIR="$HOME_DIR/Downloads"; [ -d "$LOGDIR" ] && [ -w "$LOGDIR" ] || LOGDIR=/tmp
LOG="$LOGDIR/reformat-drive-$(date +%Y%m%d-%H%M%S).log"

capture() { local secs=$1; shift; local f; f=$(mktemp /tmp/reformat.XXXXXX); "$@" >"$f" 2>&1 & local p=$! i=0 rc
  while kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i + 1)); if [ "$i" -ge "$secs" ]; then kill -9 "$p" 2>/dev/null; cat "$f"; echo "(gave up after ${secs}s: the disk does not answer)"; rm -f "$f"; return 124; fi; done
  wait "$p"; rc=$?; cat "$f"; rm -f "$f"; return "$rc"; }

run() {
echo "=== reformat_drive.sh $(date '+%Y-%m-%d %H:%M:%S') disk=$DISK label=$LABEL marker=$MARKER test=$TEST  macOS $(sw_vers -productVersion)"
INFO=$(diskutil info "$DISK" 2>&1) || die "diskutil cannot see $DISK"
f() { echo "$INFO" | sed -n "s|^ *$1: *||p" | head -1; }
SIZE=$(echo "$INFO" | sed -n 's/.*Disk Size:.*(\([0-9]*\) Bytes).*/\1/p' | head -1)
[ "$(f Whole)" = Yes ] || die "$DISK is not a whole disk"
[ "$(f 'Device Location')" = External ] || die "$DISK is not an external disk"
say "Disk $DISK: $(f 'Device / Media Name'), $((SIZE / 1000000000)) GB, $(f Protocol)"
diskutil list "$DISK" | sed 's/^/    /'

# ---- 1. the footage must be safe elsewhere
if [ "$NOINV" -eq 0 ]; then
  INV=$(ls -t "$HOME_DIR"/Downloads/jowua-inventory-*.log 2>/dev/null | head -1)
  [ -n "$INV" ] || die "no inventory log in ~/Downloads: run inventory_jowua.py first (nothing was erased)"
  [ -n "$(find "$INV" -mtime -3 2>/dev/null)" ] || die "the newest inventory log is older than 3 days ($INV): run inventory_jowua.py again"
  grep -q "RESULT: every footage file has a verified copy elsewhere" "$INV" || die "the inventory does not say every file is safe ($INV): nothing was erased"
  say "Step 1/5: inventory $INV says every footage file has a verified copy elsewhere"
else say "Step 1/5: inventory check skipped (--no-inventory-check)"; fi

if [ "$YES" -ne 1 ]; then
  printf 'This ERASES %s (%s GB) for good. Type "erase %s" to go ahead: ' "$DISK" "$((SIZE / 1000000000))" "$DISK"; read -r ans; [ "$ans" = "erase $DISK" ] || die "not confirmed: nothing was erased"
fi

# ---- 2. unmount, erase
say "Step 2/5: unmount $DISK"
OUT=$(capture 60 diskutil unmountDisk "$DISK") || die "$DISK could not be unmounted (something has it open; nothing was erased):
$OUT"
say "Step 3/5: erase as exFAT (MBR) named $LABEL"
OUT=$(capture 600 diskutil eraseDisk ExFAT "$LABEL" MBRFormat "$DISK"); RC=$?; echo "$OUT" | tail -4
[ "$RC" -eq 0 ] || die "the erase failed (status $RC)"
PART="${DISK}s1"; MP=$(diskutil info "$PART" | sed -n 's|^ *Mount Point: *||p' | head -1); UUID=$(diskutil info "$PART" | sed -n 's|^ *Volume UUID: *||p' | head -1)
[ -n "$MP" ] && [ -d "$MP" ] || die "the new volume did not mount ($PART)"
say "  new volume $PART mounted at $MP  (UUID $UUID)"
mkdir -p "$MP/TeslaCam" && { [ "$MARKER" = none ] || mkdir -p "$MP/$MARKER"; }; touch "$MP/.metadata_never_index"
say "Step 4/5: created TeslaCam/${MARKER:+ and $MARKER/}"

# ---- 3. surface test
STATUS=0
if [ "$TEST" = none ]; then say "Step 5/5: surface test skipped"; else
  FREE_KB=$(df -k "$MP" | awk 'NR==2 {print $4}'); WANT_GIB=$(( FREE_KB / 1048576 - 2 ))
  [ "$TEST" = all ] || { [ "$TEST" -lt "$WANT_GIB" ] && WANT_GIB=$TEST; }
  [ "$WANT_GIB" -ge 1 ] || die "no room to test"
  say "Step 5/5: surface test: write $WANT_GIB GiB in 1 GiB files, read them back, compare"
  PAT=$(mktemp /tmp/reformat-pattern.XXXXXX); dd if=/dev/urandom of="$PAT" bs=1m count="${PATMB:-1024}" 2>/dev/null; WANTSUM=$(shasum "$PAT" | cut -d' ' -f1)
  D="$MP/surfacetest"; mkdir -p "$D"; W0=$(date +%s); N=0
  while [ "$N" -lt "$WANT_GIB" ]; do
    N=$((N + 1)); dd if="$PAT" of="$D/t$(printf %04d $N)" bs=4m 2>/dev/null || { say "  WRITE FAILED at file $N"; STATUS=2; break; }
    [ $((N % 50)) -eq 0 ] && say "  written $N GiB ($(( N * 1073741824 / ($(date +%s) - W0 + 1) / 1000000 )) MB/s)"
  done
  sync; purge 2>/dev/null
  say "  read back and compare $N files"; R0=$(date +%s); BAD=0; SLOW=0; RATES=""
  for i in $(seq 1 "$N"); do
    t1=$(date +%s); GOT=$(dd if="$D/t$(printf %04d $i)" bs=4m 2>/dev/null | shasum | cut -d' ' -f1); t2=$(date +%s); dt=$((t2 - t1)); [ "$dt" -lt 1 ] && dt=1
    if [ "$GOT" != "$WANTSUM" ]; then BAD=$((BAD + 1)); say "  MISMATCH or read error in file $i (about $((i - 1)) GiB into the test)"; fi
    if [ "$dt" -gt 20 ]; then SLOW=$((SLOW + 1)); say "  SLOW read: file $i took ${dt} s (about $((i - 1)) GiB in)"; fi
    [ $((i % 50)) -eq 0 ] && say "  checked $i GiB, $BAD bad, $SLOW slow ($(( i * 1073741824 / ($(date +%s) - R0 + 1) / 1000000 )) MB/s)"
  done
  rm -rf "$D" "$PAT"; sync
  say "surface test: $N GiB written; $BAD bad files; $SLOW slow reads"
  [ "$BAD" -eq 0 ] || STATUS=2
fi
say "free space now: $(df -h "$MP" | awk 'NR==2 {print $4}') of $(df -h "$MP" | awk 'NR==2 {print $2}')"
if [ "$STATUS" -eq 0 ]; then say "RESULT: $DISK is a fresh exFAT drive named $LABEL at $MP, ready for the car. New volume UUID: $UUID (register it in the app). Log: $LOG"
else say "RESULT: erased, but the surface test found problems: do not trust this drive for footage. Log: $LOG"; fi
return "$STATUS"
}
run 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}; echo "Log: $LOG"; exit "$RC"
