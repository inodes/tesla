#!/bin/bash
# image_disk.sh - make a raw, byte-for-byte image of a whole disk (for example a TeslaCam drive whose file system is damaged), with checks before and after.
#
#   ./image_disk.sh DISK IMAGE [--check] [--repair] [--part diskNsM] [--yes] [--force] [--no-verify]
#
#   DISK        the whole disk, as macOS names it: disk2 (not disk2s1; see `diskutil list`). /dev/disk2 and /dev/rdisk2 are accepted too.
#   IMAGE       the file to write, on ANOTHER disk with room for the whole thing: /Volumes/LaCie1/jowua-raw.img
#   --check     only run the checks (what the disk is, room for the image, can the disk be read at the start, middle and end); nothing is unmounted or written
#   --repair    after a CLEAN image, repair the file system on the disk itself (fsck_exfat -y), then check it again. Without it the file system is only checked, never changed
#   --part P    the partition to check and repair, if the disk has more than one (default: its only non-EFI partition)
#   --yes       do not ask for the disk name to be typed before starting
#   --force     allow an internal disk, or replacing an image that exists
#   --no-verify skip the comparison of 8 samples of the disk with the image at the end
#
# What it does, in order: refuses anything that is not a whole disk or that holds the image itself; checks the room; reads 64 MB at the start, the middle and the end of the
# disk (a disk that does not answer is reported, not waited on); asks you to type the disk name; unmounts it; copies it with dd (bs=4m, conv=noerror,sync: a bad block is
# zero-filled and counted, not fatal) while showing progress; checks dd's result, the errors it printed and the size of the image; compares 8 samples of 64 MB; then
# checks the file system read-only and, with --repair, repairs it and checks again. Run it with the Claude app QUIT (its workspace keeps connected disks mounted).
# The disk is only ever READ, except by --repair. It is left unmounted. Everything printed here, with the time, is also kept in IMAGE.log, and dd's own messages (read errors) in IMAGE.dd.log.
# Exit status: 0 = a complete image, 1 = a check failed or the copy did not finish, 2 = copied, but with read errors or a sample that differs.
#
# If the unmount is refused, something has the disk open (a Claude connected folder, Finder, Spotlight): the message says which process.

set -u -o pipefail
die()  { echo "ERROR: $*" >&2; exit 1; }
say()  { echo "$(date '+%H:%M:%S') • $*"; }
usage(){ sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }

[ "$(uname)" = Darwin ] || die "this is for macOS (it uses diskutil)"
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"        # reading a raw disk needs root

DISK="" IMG="" CHECK=0 YES=0 FORCE=0 VERIFY=1 REPAIR=0 PART=""
while [ $# -gt 0 ]; do
  a=$1; shift
  case "$a" in
    --part) [ $# -gt 0 ] || die "--part needs a partition like disk2s1"; PART=${1#/dev/}; shift ;;
    --check) CHECK=1 ;; --repair) REPAIR=1 ;; --yes) YES=1 ;; --force) FORCE=1 ;; --no-verify) VERIFY=0 ;; -h|--help) usage 0 ;;
    -*) die "unknown option: $a" ;;
    *) if [ -z "$DISK" ]; then DISK="$a"; elif [ -z "$IMG" ]; then IMG="$a"; else die "unexpected argument: $a"; fi ;;
  esac
done
[ -n "$DISK" ] && [ -n "$IMG" ] || usage 1
DISK=${DISK#/dev/}; case "$DISK" in rdisk*) DISK=${DISK#r} ;; esac
[[ "$DISK" =~ ^disk[0-9]+$ ]] || die "'$DISK' is not a whole disk like disk2 (a partition like disk2s1 will not do)"
RAW="/dev/r$DISK"
DIR=$(dirname "$IMG")
[ -d "$DIR" ] && [ -w "$DIR" ] || die "cannot write in $DIR (is the disk mounted?)"
LOG="$IMG.log"; DDLOG="$IMG.dd.log"

run() {
echo "=== image_disk.sh $(date '+%Y-%m-%d %H:%M:%S')  disk=$DISK image=$IMG check=$CHECK verify=$VERIFY force=$FORCE  macOS $(sw_vers -productVersion)  user=$(logname 2>/dev/null || echo "${SUDO_USER:-root}")"
# ---- the disk
INFO=$(diskutil info "$DISK" 2>&1) || die "diskutil cannot see $DISK:
$INFO"
field() { echo "$INFO" | sed -n "s|^ *$1: *||p" | head -1; }
SIZE=$(echo "$INFO" | sed -n 's/.*Disk Size:.*(\([0-9]*\) Bytes).*/\1/p' | head -1)
[[ "$SIZE" =~ ^[0-9]+$ ]] && [ "$SIZE" -gt 0 ] || die "cannot read the size of $DISK"
[ "$(field Whole)" = "Yes" ] || die "$DISK is not a whole disk (diskutil says Whole: $(field Whole))"
LOC=$(field "Device Location")
if [ "$LOC" != "External" ] && [ "$FORCE" -ne 1 ]; then die "$DISK is not an external disk (location: ${LOC:-unknown}); use --force only if you are sure"; fi
echo "--- diskutil info $DISK ---"; echo "$INFO"; echo "---"
say "Disk:   $DISK  $(field "Device / Media Name")  $(field "Disk Size" | sed 's/ (.*//')  ($SIZE bytes)  location: ${LOC:-?}"
say "Image:  $IMG"

# ---- the image: where, and room
if [ -e "$IMG" ] && [ "$FORCE" -ne 1 ]; then die "$IMG already exists (use --force to replace it)"; fi
DEST_DEV=$(df "$DIR" | awk 'NR==2{print $1}')
case "$DEST_DEV" in "/dev/$DISK"|"/dev/${DISK}"s*) die "the image would be written onto the disk being copied ($DEST_DEV)" ;; esac
FREE=$(( $(df -k "$DIR" | awk 'NR==2{print $4}') * 1024 ))
NEED=$(( SIZE + 1073741824 ))
[ "$FREE" -ge "$NEED" ] || die "not enough room in $DIR: $((FREE / 1073741824)) GiB free, $((NEED / 1073741824)) GiB needed (the disk plus 1 GiB)"
say "Room:   $((FREE / 1073741824)) GiB free for $((SIZE / 1073741824)) GiB, on $DEST_DEV"

# ---- can it be read at all? (never waits on a disk that does not answer)
limited() { local secs=$1; shift; "$@" >/dev/null 2>&1 & local p=$! i=0
  while kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i + 1)); if [ "$i" -ge "$secs" ]; then kill -9 "$p" 2>/dev/null; return 124; fi; done; wait "$p"; }
MB=$(( SIZE / 1048576 ))
for pos in 0 $(( MB / 2 )) $(( MB - 128 )); do
  limited 20 dd if="$RAW" of=/dev/null bs=1m skip="$pos" count=64 || die "reading 64 MB at ${pos} MB of $DISK failed or took over 20 s: the disk is not answering (nothing was changed)"
done
say "Reads:  64 MB at the start, middle and end all answered"
[ "$CHECK" -eq 1 ] && { say "Checks passed (--check: nothing was unmounted or written)."; exit 0; }

# ---- the go-ahead
if [ "$YES" -ne 1 ]; then
  printf "Type the disk name (%s) to copy it to %s: " "$DISK" "$IMG"; read -r ans
  [ "$ans" = "$DISK" ] || die "not confirmed"
fi

# ---- unmount (not a force: if something has it open, say so)
if ! OUT=$(diskutil unmountDisk "$DISK" 2>&1); then
  echo "$OUT" >&2
  echo "$OUT" | grep -o 'PID [0-9]* ([^)]*)' >&2
  if echo "$OUT" | grep -q "Virtualization"; then die "$DISK could not be unmounted: it is held by the Claude app's workspace. Quit Claude (Cmd-Q) or remove the disk from its connected folders, then run this again"; fi
  die "$DISK could not be unmounted. A process has it open: quit it (a Finder window on the disk, Spotlight, an app) and run this again"
fi
say "Unmounted $DISK"

# ---- the copy
: > "$DDLOG"
BEGIN=$(date +%s)
trap 'echo; echo "Stopped. A partial image is at $IMG (not usable as it is). Nothing on $DISK was changed."; kill "$DDPID" 2>/dev/null; exit 1' INT TERM
dd if="$RAW" of="$IMG" bs=4m conv=noerror,sync 2>>"$DDLOG" &
DDPID=$!
caffeinate -i -w "$DDPID" 2>/dev/null &                                 # keep the Mac awake while it runs
say "Copying (about an hour for 1 TB; the Mac stays awake; Ctrl-C stops it). Log: $LOG"
while kill -0 "$DDPID" 2>/dev/null; do
  sleep 30
  CUR=$(stat -f %z "$IMG" 2>/dev/null || echo 0); NOW=$(date +%s); EL=$(( NOW - BEGIN )); [ "$EL" -lt 1 ] && EL=1
  RATE=$(( CUR / EL / 1048576 )); PCT=$(( CUR * 100 / SIZE ))
  if [ "$RATE" -gt 0 ]; then LEFT=$(( (SIZE - CUR) / 1048576 / RATE / 60 )); else LEFT="?"; fi
  printf "  %3d%%  %d GiB  %d MB/s  %s min left  %d read error(s) so far\n" "$PCT" $(( CUR / 1073741824 )) "$RATE" "$LEFT" "$(grep -ci 'error' "$DDLOG")"
done
wait "$DDPID"; RC=$?
trap - INT TERM
ERRS=$(grep -ci 'error' "$DDLOG")
echo "--- dd output ($DDLOG) ---"; cat "$DDLOG"; echo "---"
SZ=$(stat -f %z "$IMG" 2>/dev/null || echo 0)
say "dd finished with status $RC after $(( ($(date +%s) - BEGIN) / 60 )) min; $ERRS read error line(s); image $SZ bytes (disk $SIZE)"
[ "$RC" -eq 0 ] || { tail -5 "$DDLOG" >&2; die "dd did not finish (status $RC); the image is incomplete"; }
# conv=sync pads the last block to 4 MiB, so the image may be up to 4 MiB longer than the disk, never shorter
if [ "$SZ" -lt "$SIZE" ] || [ "$SZ" -gt $(( SIZE + 4194304 )) ]; then die "the image is $SZ bytes, the disk is $SIZE: the copy is not complete"; fi

STATUS=0
[ "$ERRS" -eq 0 ] || { echo "WARNING: dd reported read errors (see $DDLOG): those blocks are zeros in the image." >&2; STATUS=2; }

# ---- compare samples of the disk with the image
if [ "$VERIFY" -eq 1 ]; then
  say "Comparing 8 samples of 64 MB (disk and image)..."
  BAD=0
  for i in 0 1 2 3 4 5 6 7; do
    pos=$(( (MB - 64) * i / 7 ))
    A=$(dd if="$RAW" bs=1m skip="$pos" count=64 2>/dev/null | shasum | cut -d' ' -f1)
    B=$(dd if="$IMG" bs=1m skip="$pos" count=64 2>/dev/null | shasum | cut -d' ' -f1)
    if [ "$A" = "$B" ]; then echo "  at ${pos} MB: same"; else echo "  at ${pos} MB: DIFFERENT" >&2; BAD=$((BAD + 1)); fi
  done
  [ "$BAD" -eq 0 ] || { echo "WARNING: $BAD sample(s) differ." >&2; STATUS=2; }
fi

if [ "$STATUS" -eq 0 ]; then say "The image $IMG is a complete copy of $DISK."
else echo "The image has warnings (above). $DISK will not be repaired." >&2; fi

# ---- the file system on the disk (read-only check; repair only with --repair and only after a clean image)
if [ -z "$PART" ]; then
  PARTS=$(diskutil list "$DISK" 2>/dev/null | awk -v d="$DISK" '$NF ~ ("^" d "s[0-9]+$") && $0 !~ /EFI/ {print $NF}')
  if [ "$(echo "$PARTS" | grep -c .)" -eq 1 ]; then PART=$PARTS; fi
fi
if [ -z "$PART" ]; then
  echo "WARNING: could not tell which partition of $DISK holds the file system (use --part diskNsM); it was not checked." >&2; STATUS=2
else
  say "Checking the file system on $PART (read-only: fsck_exfat -n)..."
  fsck_exfat -n "/dev/r$PART" 2>&1; FSRC=$?
  if [ "$FSRC" -eq 0 ]; then say "File system on $PART is clean."
  else
    say "File system on $PART needs repair (fsck_exfat exit code $FSRC)."
    if [ "$REPAIR" -ne 1 ]; then say "Not repaired (add --repair to do it, now that the image exists)."
    elif [ "$STATUS" -ne 0 ]; then echo "WARNING: not repairing: the image had warnings." >&2
    else
      say "Repairing $PART (fsck_exfat -y)..."
      fsck_exfat -y "/dev/r$PART" 2>&1; say "Repair finished with exit code $?"
      say "Checking again (fsck_exfat -n)..."
      if fsck_exfat -n "/dev/r$PART" 2>&1; then say "File system on $PART is now clean."
      else echo "WARNING: $PART still needs repair after the repair. The image of the original is kept: $IMG" >&2; STATUS=2; fi
    fi
  fi
fi
say "Log: $LOG"
[ -n "$PART" ] && say "To look at the disk read-only: diskutil mount readOnly $PART"
exit "$STATUS"
}

run 2>&1 | tee -a "$LOG"
exit "${PIPESTATUS[0]}"
