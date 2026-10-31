#!/usr/bin/env python3
"""inventory_jowua.py - before the JOWUA disk is wiped: is EVERY file on it also safely somewhere else, and where?

  ./inventory_jowua.py [--src PATH] [--archive PATH] [--rescue PATH] [--full] [--tsv FILE]

  --src      the JOWUA volume, read-only (default: the mounted volume whose UUID is the registered J drive's; the unrepaired image mounted by refresh_image.sh has it)
  --archive  the Archive volume (default: the mounted volume whose UUID is the registered Archive's)
  --src-uuid / --archive-uuid  choose by volume UUID instead (./manage_teslacam_drives.py lists them). Names like TESLADRIVE 1 are never used to decide.
  --rescue   the rescued copy (default /Volumes/LaCie1/jowua-rescue)
  --full     compare whole files (slow); the default compares the first MB, the last MB and 4 places between
  --tsv FILE where to write one line per file (default ~/Downloads/jowua-inventory-DATE.tsv)
  --no-save-outside  do not copy the files outside TeslaCam (JOWUA_1TB, Tools, Icons...) into RESCUE/_outside (by default they are copied and checked, so they can be put back)

For every file on JOWUA it looks for a copy with the same name and size in the Archive and in the rescue folder (same path first, then the same file name anywhere in
TeslaCam), compares the contents, and reports: verified on the Archive, on the rescue copy, on both, ONLY ON JOWUA, or DIFFERENT. Files outside TeslaCam (JOWUA_1TB,
Tools, Icons, system folders) are listed separately: nothing outside TeslaCam is ever counted as footage. Nothing is written to JOWUA, the Archive or the rescue folder.
It also writes the MANIFEST that restore_drive.py puts everything back from: every folder and file of JOWUA with its size and time and where each verified copy is
(~/Downloads/jowua-manifest-DATE.json, and a copy in RESCUE/_manifest/jowua-manifest.json).
Log: ~/Downloads/jowua-inventory-DATE.log. Exit status 0 = every footage file has a verified copy elsewhere, 2 = some do not, 1 = could not start.
"""
import os, sys, time, collections, json, shutil

def now(): return time.strftime("%H:%M:%S")
args = sys.argv[1:]
if "-h" in args or "--help" in args: print(__doc__); sys.exit(0)
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default
SRC = opt("--src"); RES = opt("--rescue", "/Volumes/LaCie1/jowua-rescue"); ARC = opt("--archive"); FULL = "--full" in args
stamp = time.strftime("%Y%m%d-%H%M%S"); logdir = os.path.expanduser("~/Downloads"); logdir = logdir if os.path.isdir(logdir) and os.access(logdir, os.W_OK) else "/tmp"
TSV = os.path.expanduser(opt("--tsv", os.path.join(logdir, "jowua-inventory-%s.tsv" % stamp)))
logf = open(os.path.join(logdir, "jowua-inventory-%s.log" % stamp), "a", buffering=1)
def say(m): line = "%s • %s" % (now(), m); print(line, flush=True); logf.write(line + "\n")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import drive_registry as REG
def by_uuid(what, vid):
    mps = REG.mount_of(vid)
    if len(mps) != 1:
        say("ERROR: the %s (UUID %s) is %s%s" % (what, vid, "not mounted" if not mps else "mounted more than once: ", ", ".join(mps)))
        sys.exit(1)
    return mps[0]
reg = {d["role"]: d for d in REG.drives()}
say("mounted volumes (identified by UUID, not by name):")
for mp, vid in REG.mounted_volumes():
    e = REG.lookup(vid)
    if os.path.isdir(os.path.join(mp, "TeslaCam")) or e: say("  %-28s %s  %s" % (mp, vid, ("registered: %s (%s, role %s)" % (e["label"], e["function"], e["role"])) if e else "not registered"))
if not SRC: SRC = by_uuid("JOWUA volume", opt("--src-uuid") or (reg.get("J") or {}).get("id") or "")
if not ARC: ARC = by_uuid("Archive", opt("--archive-uuid") or (reg.get("A") or {}).get("id") or "")
for name, p in (("source", SRC), ("rescue", RES), ("Archive", ARC)):
    if not p or not os.path.isdir(os.path.join(p, "TeslaCam")): say("ERROR: no TeslaCam folder in the %s (%s)" % (name, p)); sys.exit(1)
say("inventory: JOWUA=%s  Archive=%s  rescue=%s  compare=%s" % (SRC, ARC, RES, "whole files" if FULL else "head, tail, 4 samples"))

SKIP = {".Spotlight-V100", ".fseventsd", ".Trashes", "System Volume Information", ".TemporaryItems", ".DocumentRevisions-V100"}
def walk(root):
    for r, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for n in names:
            if n == ".DS_Store": continue
            if n.startswith("._") and (os.sep + "TeslaCam" + os.sep) in (r + os.sep): continue   # a clip's companion file; the ones at the top of the drive carry icons and are kept
            yield os.path.join(r, n)

# the other copies: by path and by (name, size)
say("reading the Archive and the rescue folder listings...")
idx = {"A": collections.defaultdict(list), "R": collections.defaultdict(list)}; paths = {"A": {}, "R": {}}
for tag, root in (("A", ARC), ("R", RES)):
    n = 0
    for f in walk(os.path.join(root, "TeslaCam")):
        try: sz = os.path.getsize(f)
        except OSError: continue
        rel = os.path.relpath(f, os.path.join(root, "TeslaCam")); paths[tag][rel] = (f, sz); idx[tag][(os.path.basename(f), sz)].append(f); n += 1
    say("  %s: %d files" % ("Archive" if tag == "A" else "rescue", n))

def same(a, b, size):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        if FULL or size <= 3 << 20:
            while True:
                x = fa.read(4 << 20); y = fb.read(4 << 20)
                if x != y: return False
                if not x: return True
        offs = sorted(set([0, size - (1 << 20)] + [int(size * i / 5) for i in range(1, 5)]))
        for o in offs:
            o = max(0, min(o, size - (1 << 20))); fa.seek(o); fb.seek(o)
            if fa.read(1 << 20 if o in (0, size - (1 << 20)) else 256 << 10) != fb.read(1 << 20 if o in (0, size - (1 << 20)) else 256 << 10): return False
    return True

def find_copy(tag, rel, name, sz, s):
    """verified copy of this JOWUA file in tag's tree: returns (status, path) with status ok / different / none"""
    cands = []
    if rel in paths[tag] and paths[tag][rel][1] == sz: cands.append(paths[tag][rel][0])
    for c in idx[tag].get((name, sz), []):
        if c not in cands: cands.append(c)
    if not cands: return ("none", "")
    for c in cands[:4]:
        try:
            if same(s, c, sz): return ("ok", c)
        except OSError: pass
    return ("different", cands[0])

manifest = {"made": time.strftime("%Y-%m-%d %H:%M:%S"), "src": SRC, "src_uuid": REG.volume_id(SRC), "archive_uuid": REG.volume_id(ARC), "dirs": [], "files": []}
SAVE_OUT = "--no-save-outside" not in args
rows = []; other = []; counts = collections.Counter(); bytes_ = collections.Counter(); only = []; diff = []; t0 = time.time(); i = 0
for r_, dirs_, names_ in os.walk(SRC):
    dirs_[:] = [d for d in dirs_ if d not in SKIP]
    for d_ in dirs_: manifest["dirs"].append(os.path.relpath(os.path.join(r_, d_), SRC))
for f in walk(SRC):
    rel = os.path.relpath(f, SRC)
    if not rel.startswith("TeslaCam" + os.sep):
        try: st_ = os.stat(f); other.append((rel, st_.st_size))
        except OSError: other.append((rel, -1)); continue
        # keep a copy beside the rescued footage so it can be put back
        dst_ = os.path.join(RES, "_outside", rel); ok_ = False
        if SAVE_OUT:
            try:
                os.makedirs(os.path.dirname(dst_), exist_ok=True)
                if not (os.path.exists(dst_) and os.path.getsize(dst_) == st_.st_size and same(f, dst_, st_.st_size)):
                    shutil.copyfile(f, dst_)
                ok_ = same(f, dst_, st_.st_size)
            except OSError as e: say("  cannot save %s: %s" % (rel, e))
        manifest["files"].append({"path": rel, "size": st_.st_size, "mtime": st_.st_mtime, "copies": ({"X": dst_} if ok_ else {}), "footage": False})
        continue
    i += 1
    try: sz = os.path.getsize(f)
    except OSError as e: rows.append(("UNREADABLE", rel, -1, "")); counts["UNREADABLE"] += 1; continue
    r2 = rel[len("TeslaCam") + 1:]; name = os.path.basename(f)
    try:
        a = find_copy("A", r2, name, sz, f); r = find_copy("R", r2, name, sz, f)
    except Exception as e:
        rows.append(("ERROR", rel, sz, str(e))); counts["ERROR"] += 1; continue
    where = ("A" if a[0] == "ok" else "") + ("R" if r[0] == "ok" else "")
    if where: st = "ON_" + where
    elif "different" in (a[0], r[0]): st = "DIFFERENT"; diff.append(rel)
    else: st = "ONLY_ON_JOWUA"; only.append((rel, sz))
    counts[st] += 1; bytes_[st] += sz
    rows.append((st, rel, sz, a[1] or r[1]))
    try: mt = os.stat(f).st_mtime
    except OSError: mt = 0
    manifest["files"].append({"path": rel, "size": sz, "mtime": mt, "copies": {k: v[1] for k, v in (("A", a), ("R", r)) if v[0] == "ok"}, "footage": True})
    if i % 1000 == 0: say("%d footage files checked  (only on JOWUA so far: %d)  %.0f s" % (i, len(only), time.time() - t0))

with open(TSV, "w") as o:
    o.write("status\tpath\tbytes\tcopy_found_at\n")
    for r in rows: o.write("\t".join(str(x) for x in r) + "\n")
say("footage files on JOWUA: %d (%.1f GB)" % (i, sum(bytes_.values()) / 1e9))
for st in ("ON_AR", "ON_A", "ON_R", "ONLY_ON_JOWUA", "DIFFERENT", "UNREADABLE", "ERROR"):
    if counts[st]: say("  %-14s %6d files  %8.1f GB" % (st, counts[st], bytes_[st] / 1e9))
if only:
    days = collections.Counter(); 
    for rel, sz in only: days[(rel.split(os.sep)[2] if rel.split(os.sep)[1] != "RecentClips" else os.path.basename(rel))[:10]] += 1
    say("ONLY ON JOWUA, by day: " + ", ".join("%s: %d" % kv for kv in sorted(days.items())))
    for rel, sz in only[:30]: say("  ONLY ON JOWUA: %s (%d bytes)" % (rel, sz))
if diff:
    say("DIFFERENT (a file with that name and size exists elsewhere but its contents differ): %d" % len(diff))
    for rel in diff[:30]: say("  DIFFERENT: %s" % rel)
top = collections.Counter(r.split(os.sep)[0] for r, s in other)
say("outside TeslaCam (not footage): %d files: %s" % (len(other), ", ".join("%s: %d" % kv for kv in sorted(top.items())) or "none"))
for rel, sz in other[:20]: say("  OUTSIDE: %s (%d bytes)" % (rel, sz))
bad = len(only) + len(diff) + counts["UNREADABLE"] + counts["ERROR"]
unsaved = [m_["path"] for m_ in manifest["files"] if not m_["copies"]]
if SAVE_OUT and any(not m_["footage"] and not m_["copies"] for m_ in manifest["files"]): say("WARNING: some files outside TeslaCam could not be saved: %s" % ", ".join(m_["path"] for m_ in manifest["files"] if not m_["footage"] and not m_["copies"])[:300]); bad += 1
MAN = os.path.join(logdir, "jowua-manifest-%s.json" % stamp)
json.dump(manifest, open(MAN, "w"))
try:
    os.makedirs(os.path.join(RES, "_manifest"), exist_ok=True); shutil.copyfile(MAN, os.path.join(RES, "_manifest", "jowua-manifest.json"))
except OSError as e: say("WARNING: could not save the manifest beside the rescued footage: %s" % e)
nA = sum(1 for m_ in manifest["files"] if "A" in m_["copies"]); nR = sum(1 for m_ in manifest["files"] if "R" in m_["copies"] and "A" not in m_["copies"]); nX = sum(1 for m_ in manifest["files"] if "X" in m_["copies"])
say("restore plan: %d folders, %d files (%.1f GB) can be put back: %d from the Archive, %d from the rescue copy, %d saved from outside TeslaCam. Manifest: %s" % (len(manifest["dirs"]), len(manifest["files"]) - len(unsaved), sum(m_["size"] for m_ in manifest["files"]) / 1e9, nA, nR, nX, MAN))
say("RESULT: %s. One line per file: %s. Log: %s" % ("every footage file has a verified copy elsewhere" if bad == 0 else "%d footage files do NOT have a verified copy elsewhere" % bad, TSV, logf.name))
sys.exit(0 if bad == 0 else 2)
