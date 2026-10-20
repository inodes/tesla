#!/usr/bin/env python3
"""restore_drive.py - put everything back on a fresh drive from the manifest written by inventory_jowua.py, and prove it is all there.

NAME
    restore_drive.py - copy a drive's files back from their verified copies and check every one

SYNOPSIS
    restore_drive.py --dest PATH [--manifest FILE] [--all] [--dry-run] [--no-verify]

DESCRIPTION
    Reads the manifest (default: the newest ~/Downloads/jowua-manifest-*.json): every folder and file the old drive had, with its size and time and where its
    verified copies are (Archive, rescue folder, saved outside-TeslaCam files). Files that are already on the Archive are NOT put back (the Archive is their
    home; --all puts them back too). The rest are copied from a copy that is present, with the file's time kept, and every one is then read back from the new
    drive and compared with the copy it came from. The report says where each group of backups lives.
    A file already on the new drive with the right size is not copied again, but is still checked, so an interrupted run can be run again.
    With --dry-run nothing is written: it only proves the plan, that every copy it would use is there, has the right size, and that everything fits.
    Nothing is read from or written to the old drive. It stops at once if DEST is not a mounted volume.

OPTIONS
    --dest PATH      the new drive, mounted (for example /Volumes/JOWUA)
    --manifest FILE  the manifest to use
    --all            also put back the files that are already on the Archive
    --dry-run        check the plan only
    --no-verify      skip the read-back (not recommended)

EXIT STATUS
    0 everything is back and matches   2 some files are missing, did not copy or do not match   1 could not start

FILES
    ~/Downloads/restore-drive-DATE.log
"""
import os, sys, time, json, glob, shutil, hashlib

def now(): return time.strftime("%H:%M:%S")
args = sys.argv[1:]
if "-h" in args or "--help" in args or not args: print(__doc__); sys.exit(0 if args else 1)
def opt(n, d=None): return args[args.index(n) + 1] if n in args else d
ALL = "--all" in args; DEST = opt("--dest"); DRY = "--dry-run" in args; VERIFY = "--no-verify" not in args
logdir = os.path.expanduser("~/Downloads"); logdir = logdir if os.path.isdir(logdir) and os.access(logdir, os.W_OK) else "/tmp"
logf = open(os.path.join(logdir, "restore-drive-%s.log" % time.strftime("%Y%m%d-%H%M%S")), "a", buffering=1)
def say(m): line = "%s • %s" % (now(), m); print(line, flush=True); logf.write(line + "\n")
MAN = os.path.expanduser(opt("--manifest") or "")
if not MAN:
    c = sorted(glob.glob(os.path.join(logdir, "jowua-manifest-*.json")), key=os.path.getmtime)
    MAN = c[-1] if c else ""
if not MAN or not os.path.exists(MAN): say("ERROR: no manifest (run inventory_jowua.py first)"); sys.exit(1)
if not DEST or not os.path.isdir(DEST): say("ERROR: --dest must be a mounted drive"); sys.exit(1)
if DEST.rstrip("/") in ("/", "/Volumes", os.path.expanduser("~")): say("ERROR: --dest looks wrong: %s" % DEST); sys.exit(1)
m = json.load(open(MAN)); allfiles = m["files"]; alldirs = m["dirs"]
onarc = [f for f in allfiles if "A" in f["copies"]]
files = allfiles if ALL else [f for f in allfiles if "A" not in f["copies"]]
keep = set(os.path.dirname(f["path"]) for f in files)
dirs = [d for d in alldirs if d.count(os.sep) <= 1 or d in keep or any(k.startswith(d + os.sep) for k in keep)]
say("restore_drive: manifest %s (made %s): %d files, %.1f GB in all -> %s%s" % (MAN, m.get("made"), len(allfiles), sum(f["size"] for f in allfiles) / 1e9, DEST, "  [dry run]" if DRY else ""))
if not ALL: say("not put back: %d files (%.1f GB) that are already on the Archive (use --all to put them back too)" % (len(onarc), sum(f["size"] for f in onarc) / 1e9))
roots = {"Archive": set(), "rescue copy": set(), "saved outside files": set()}
for f in files:
    for k, nm in (("A", "Archive"), ("R", "rescue copy"), ("X", "saved outside files")):
        if f["copies"].get(k): roots[nm].add(os.path.dirname(os.path.dirname(f["copies"][k])) if k != "X" else os.path.dirname(f["copies"][k]))
say("%d files to put back; backups live in: %s" % (len(files), "; ".join("%s: %s" % (k, sorted(v)[0] if v else "-") for k, v in roots.items() if v) or "-"))

# ---- the plan: which copy each file comes from
plan = []; nocopy = []; wrong = []
for f in files:
    use = None
    for k in ("A", "R", "X"):
        p = f["copies"].get(k)
        if not p: continue
        try:
            if os.path.getsize(p) == f["size"]: use = p; break
            wrong.append((f["path"], p))
        except OSError: pass
    if use: plan.append((f, use))
    else: nocopy.append(f["path"])
src_count = {k: sum(1 for f, u in plan if f["copies"].get(k) == u) for k in ("A", "R", "X")}
say("plan: %d files from the Archive, %d from the rescue copy, %d from the saved outside files; no usable copy for %d; copy of the wrong size for %d" % (src_count["A"], src_count["R"], src_count["X"], len(nocopy), len(wrong)))
for p in nocopy[:20]: say("  NO COPY: %s" % p)
for p, c in wrong[:10]: say("  WRONG SIZE: %s at %s" % (p, c))
need = sum(f["size"] for f, u in plan if not (os.path.exists(os.path.join(DEST, f["path"])) and os.path.getsize(os.path.join(DEST, f["path"])) == f["size"]))
free = shutil.disk_usage(DEST).free
say("to copy: %.1f GB; free on %s: %.1f GB" % (need / 1e9, DEST, free / 1e9))
if need + 2e9 > free: say("ERROR: not enough room on %s" % DEST); sys.exit(1)
if nocopy: say("WARNING: %d files have no usable copy: do not wipe the old drive" % len(nocopy))
if DRY:
    say("RESULT: dry run %s. Nothing was written." % ("OK: the whole drive can be put back" if not nocopy and not wrong else "found problems (above)")); sys.exit(0 if not nocopy and not wrong else 2)

# ---- copy
for d in dirs: os.makedirs(os.path.join(DEST, d), exist_ok=True)
sums = {}; copied = skipped = failed = 0; nb = 0; t0 = time.time()
for i, (f, src) in enumerate(plan, 1):
    dst = os.path.join(DEST, f["path"])
    try:
        if os.path.exists(dst) and os.path.getsize(dst) == f["size"]: skipped += 1
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True); h = hashlib.sha1(); tmp = dst + ".part"
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                while True:
                    b = fi.read(4 << 20)
                    if not b: break
                    h.update(b); fo.write(b)
            if os.path.getsize(tmp) != f["size"]: raise IOError("copied %d of %d bytes" % (os.path.getsize(tmp), f["size"]))
            os.replace(tmp, dst); os.utime(dst, (f["mtime"], f["mtime"])); sums[f["path"]] = h.hexdigest(); copied += 1; nb += f["size"]
    except Exception as e:
        failed += 1; say("FAILED %s: %s" % (f["path"], e))
        if failed >= 25 and failed > copied: say("ERROR: too many failures; stopping"); break
    if i % 500 == 0 or i == len(plan):
        el = max(time.time() - t0, 1); say("copy %d/%d  copied %d  already there %d  failed %d  %.1f GB  %.0f MB/s" % (i, len(plan), copied, skipped, failed, nb / 1e9, nb / el / 1e6))

# ---- read back and compare
bad = []; vc = 0
if VERIFY:
    try: os.system("sync; purge 2>/dev/null")
    except Exception: pass
    say("verify: reading every file back from %s and comparing" % DEST); t1 = time.time()
    def sha(p):
        h = hashlib.sha1()
        with open(p, "rb") as fh:
            while True:
                b = fh.read(8 << 20)
                if not b: break
                h.update(b)
        return h.hexdigest()
    for i, (f, src) in enumerate(plan, 1):
        dst = os.path.join(DEST, f["path"])
        try:
            want = sums.get(f["path"]) or sha(src)
            if os.path.getsize(dst) != f["size"] or sha(dst) != want: bad.append(f["path"]); say("MISMATCH %s" % f["path"])
            else: vc += 1
        except Exception as e: bad.append(f["path"]); say("CANNOT CHECK %s: %s" % (f["path"], e))
        if i % 500 == 0 or i == len(plan): say("verify %d/%d  matching %d  bad %d  (%.0f s)" % (i, len(plan), vc, len(bad), time.time() - t1))

# ---- the new drive against the manifest
missing = [f["path"] for f in files if not os.path.exists(os.path.join(DEST, f["path"]))]
ok = (not nocopy and not wrong and not failed and not bad and not missing)
say("RESULT: %s. %d of %d files to put back are on %s%s; %d missing; %d did not match; %d had no copy. Log: %s" % (
    "EVERYTHING IS BACK" if ok else "NOT COMPLETE", len(files) - len(missing), len(files), DEST, " and match their copies" if VERIFY and not bad else "", len(missing), len(bad), len(nocopy), logf.name))
sys.exit(0 if ok else 2)
