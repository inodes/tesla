#!/usr/bin/env python3
"""verify_rescue.py - check the rescued copy against the source byte for byte at sampled places, list the files that differ, and (with --fix) copy them again.

  ./verify_rescue.py [SRC [DEST]] [--fix] [--full] [--since YYYY-MM-DD]

  SRC    the mounted source (default "/Volumes/TESLADRIVE 1")      DEST   the rescued copy (default /Volumes/LaCie1/jowua-rescue)
  --fix    copy every file that differs again from SRC (written to a temporary name, then renamed; SRC is never changed)
  --full   compare the whole file instead of the first MB, the last MB and 8 samples in between (reads everything: slow)
  --from-log LOG  only the files listed as "DIFFERS" in an earlier log of this script (so a fix does not re-check all 12,000 files)
  --since  only files in names dated on or after this day (default: all of them)

Why it exists: rsync skips a file whose size and time match, even if the first run copied it from a REPAIRED image whose data was wrong. This reads the same places in both
copies and compares them. Log: ~/Downloads/verify-rescue-DATE.log. Exit status 0 = everything matched (or was fixed), 2 = differences remain, 1 = could not start.
"""
import os, sys, time, shutil

def now(): return time.strftime("%H:%M:%S")
args = sys.argv[1:]
FIX = "--fix" in args; FULL = "--full" in args; SINCE = ""; FROMLOG = ""
if "--from-log" in args: FROMLOG = os.path.expanduser(args[args.index("--from-log") + 1])
if "--since" in args: SINCE = args[args.index("--since") + 1]
pos = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--since", "--from-log"))]
if "-h" in args or "--help" in args: print(__doc__); sys.exit(0)
SRC = pos[0] if len(pos) > 0 else "/Volumes/TESLADRIVE 1"; DST = pos[1] if len(pos) > 1 else "/Volumes/LaCie1/jowua-rescue"
logdir = os.path.expanduser("~/Downloads"); logdir = logdir if os.path.isdir(logdir) and os.access(logdir, os.W_OK) else "/tmp"
logf = open(os.path.join(logdir, "verify-rescue-%s.log" % time.strftime("%Y%m%d-%H%M%S")), "a", buffering=1)
def say(m): line = "%s • %s" % (now(), m); print(line, flush=True); logf.write(line + "\n")
if not os.path.isdir(os.path.join(SRC, "TeslaCam")) or not os.path.isdir(os.path.join(DST, "TeslaCam")):
    say("ERROR: need TeslaCam folders in both %s and %s" % (SRC, DST)); sys.exit(1)

CH = 256 * 1024; HEAD = 1024 * 1024
def positions(size):
    if FULL or size <= 3 * HEAD: return None
    ps = [0, size - HEAD] + [int(size * i / 9) for i in range(1, 9)]
    return sorted(set(max(0, min(p, size - HEAD)) for p in ps)), HEAD
def same(a, b, size, full=False):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        p = None if full else positions(size)
        if p is None:
            while True:
                x = fa.read(4 << 20); y = fb.read(4 << 20)
                if x != y: return False
                if not x: return True
        offs, ln = p
        for o in offs:
            fa.seek(o); fb.seek(o)
            if fa.read(ln if o in (0, size - HEAD) else CH) != fb.read(ln if o in (0, size - HEAD) else CH): return False
    return True

files = []
if FROMLOG:
    for line in open(FROMLOG, errors="replace"):
        if "DIFFERS " in line: files.append(line.split("DIFFERS ", 1)[1].strip())
    files = [f for f in files if os.path.exists(os.path.join(DST, f))]
for root, dirs, names in (os.walk(os.path.join(DST, "TeslaCam")) if not FROMLOG else []):
    for n in names:
        if n.startswith(".") or n.endswith(".part"): continue
        full = os.path.join(root, n); rel = os.path.relpath(full, DST)
        if SINCE:
            d = n[:10] if n[:2] == "20" else ""
            ev = os.path.basename(os.path.dirname(full))[:10]
            if (ev if ev[:2] == "20" else d) < SINCE: continue
        files.append(rel)
files.sort()
say("verify_rescue: %d files in %s compared with %s (%s)%s" % (len(files), DST, SRC, "whole files" if FULL else "head, tail and 8 samples", ", fixing" if FIX else ""))
bad = []; missing = []; fixed = 0; t0 = time.time()
for i, rel in enumerate(files, 1):
    s = os.path.join(SRC, rel); d = os.path.join(DST, rel)
    try:
        if not os.path.exists(s): missing.append(rel); continue
        ss = os.path.getsize(s)
        if ss != os.path.getsize(d) or not same(s, d, ss):
            bad.append(rel)
            if FIX:
                tmp = d + ".part"
                with open(s, "rb") as fi, open(tmp, "wb") as fo: shutil.copyfileobj(fi, fo, 4 << 20)
                os.replace(tmp, d); st = os.stat(s); os.utime(d, (st.st_atime, st.st_mtime))
                if same(s, d, ss, True): fixed += 1
                else: say("STILL DIFFERS after re-copy (whole file compared): %s" % rel)
    except Exception as e:
        say("ERROR %s: %s" % (rel, e)); bad.append(rel)
    if i % 500 == 0 or i == len(files):
        say("%d/%d checked  differ %d  fixed %d  (%.0f s)" % (i, len(files), len(bad), fixed, time.time() - t0))
days = {}
for r in bad: days.setdefault(os.path.basename(r)[:10] if os.path.dirname(r).endswith("RecentClips") else os.path.basename(os.path.dirname(r))[:10], 0); days[os.path.basename(r)[:10] if os.path.dirname(r).endswith("RecentClips") else os.path.basename(os.path.dirname(r))[:10]] += 1
for r in bad[:30]: say("  DIFFERS %s" % r)
for r in bad[30:]: logf.write("  DIFFERS %s\n" % r)
if bad: say("differing files by day: " + ", ".join("%s: %d" % kv for kv in sorted(days.items())))
if missing: say("not at the source (%d), e.g. %s" % (len(missing), missing[0]))
left = len(bad) - fixed
say("RESULT: %d files checked, %d differed, %d re-copied, %d still differ, %d not at the source. Log: %s" % (len(files), len(bad), fixed, left, len(missing), logf.name))
sys.exit(0 if left == 0 else 2)
