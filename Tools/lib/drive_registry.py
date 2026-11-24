"""drive_registry.py - which TeslaCam volume is which, by its own ID; what each is for is asked once and kept in config.json.

A volume is a TeslaCam drive when it holds a TeslaCam folder with the car's structure (RecentClips / SavedClips / SentryClips and
YYYY-MM-DD_HH-MM-SS-<camera>.mp4 files). Its NAME is not used for anything: macOS hands out TESLADRIVE, TESLADRIVE 1, TESLADRIVE 2 in the order the
drives were plugged in, and the car formats every drive with the same name. A drive is recognised by its volume UUID (diskutil on macOS, findmnt
on Linux) and, the first time it is seen, the user says what it is for:

    car       goes in the car and records; the source of footage
    archive   the master copy that everything is imported to and judged against ("safe to delete" means the archive has what matters)
    copy      any other copy / backup / transfer drive

with a one-word name of their own (Archive, JOWUA, ...). That goes in config.json:

    "teslacam": {"drives": [{"id": "<volume UUID>", "label": "Archive", "function": "archive", "role": "A", "size_gb": 1862, "first_seen": "2026-10-20"}]}

A drive can also be a FOLDER on a volume (for example a rescue copy kept in a folder of an ordinary disk): the entry then has `folder`, the path of its
TeslaCam folder from the volume's root ("jowua-rescue/TeslaCam"), and is found wherever that volume is mounted. A folder may take any function; one that is not the
Archive, JOWUA or the Tesla USB is only read by the app: played, read for stills and signs, never written to, moved from or deleted from.

`role` is the short stable key the commands use on their command lines (--drive A,J). The first archive is "A"; others take the first letter of
their label when free. Nothing is ever written to a drive.
"""
import datetime
import json
import os
import plistlib
import re
import shutil
import subprocess
import time

FUNCTIONS = ("car", "archive", "copy")
SUBDIRS = ("RecentClips", "SavedClips", "SentryClips")
_CLIP = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-[a-z_]+\.mp4$")


def config_path():
    import tessie_api_common as T
    return T.resolve_config_path()


def _read():
    """config.json, parsed again only when the file changed (the page asks which drives are registered every few seconds); the caller gets its own copy."""
    import copy
    import stat_memo as SM
    j = SM.file(config_path(), _load)
    return copy.deepcopy(j) if isinstance(j, dict) else {}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def drives():
    """The registered drives [dict(id, label, function, role, size_gb, first_seen[, folder])]."""
    d = _read().get("teslacam", {}).get("drives", [])
    return [x for x in d if isinstance(x, dict) and x.get("id") and x.get("role")]


def _write(entries):
    import config_backup
    path = config_path()
    config_backup.backup(path)                                  # a dated copy first (config.json is not in git)
    cfg = _read()
    cfg.setdefault("teslacam", {})["drives"] = entries
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def drive_root(path):
    path = os.path.expanduser(path).rstrip(os.sep)
    return os.path.dirname(path) if os.path.basename(path) == "TeslaCam" else path


_SOK = {}           # TeslaCam folder -> (its clip folders' times, answer)


def structure_ok(teslacam):
    """A TeslaCam folder shaped like the car's: one of the three clip folders, and any clip found has the car's file name. It looks only as far as the first clip
    (a RecentClips folder on a big drive holds tens of thousands: listing it whole took seconds on every look at the drives, with the app waiting behind it), and
    the answer is kept while the clip folders' times are unchanged."""
    try:
        subs = [(sub, os.stat(os.path.join(teslacam, sub)).st_mtime_ns) for sub in SUBDIRS if os.path.isdir(os.path.join(teslacam, sub))]
    except OSError:
        return False
    if not subs:
        return False
    key = tuple(subs)
    hit = _SOK.get(teslacam)
    if hit and hit[0] == key:
        return hit[1]
    ok = _first_clip_ok(teslacam, [sub for sub, _m in subs])
    if ok is None:                                        # the folder could not be read just now: not kept (an idle drive's folder times would never change it back)
        return False
    _SOK[teslacam] = (key, ok)
    return ok


def _first_clip_ok(teslacam, subs):
    try:
        for sub in subs:
            with os.scandir(os.path.join(teslacam, sub)) as it:
                for n, e in enumerate(it):
                    if n >= 50:
                        break
                    if e.is_dir():                                # SavedClips/<event>/ and RecentClips (flat, on the car) both hold the clips
                        with os.scandir(e.path) as it2:
                            for k, m in enumerate(it2):
                                if k >= 20:
                                    break
                                if m.name.endswith(".mp4"):
                                    return bool(_CLIP.match(m.name))
                    elif e.name.endswith(".mp4"):
                        return bool(_CLIP.match(e.name))
    except OSError:
        return None
    return True                                               # a drive with the folders and no clips yet


def _mount_point(path):
    p = os.path.realpath(path)
    while not os.path.ismount(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p


VID_TTL_S = 60.0
_VID = {}           # mount point -> (device number, when read, UUID)


def volume_id(path):
    """The volume's UUID, or None when the system cannot say (a network share, a sandbox). Asking `diskutil` takes a fraction of a second and the drive list asks it
    for every drive and, through mount_of, for every volume, on each look: the answer is kept per mount point while the volume mounted there is the same one (its
    device number) and for at most VID_TTL_S."""
    mp = _mount_point(drive_root(path))
    try:
        dev = os.stat(mp).st_dev
    except OSError:
        _VID.pop(mp, None)
        return None
    hit = _VID.get(mp)
    if hit and hit[0] == dev and time.time() - hit[1] < VID_TTL_S:
        return hit[2]
    v = _read_volume_id(mp)
    if v:
        _VID[mp] = (dev, time.time(), v)            # an answer of None (diskutil busy) is not kept: asked again next time
    return v


def _read_volume_id(mp):
    try:
        if shutil.which("diskutil"):
            out = subprocess.run(["diskutil", "info", "-plist", mp], capture_output=True, timeout=20, stdin=subprocess.DEVNULL).stdout
            info = plistlib.loads(out)
            return str(info.get("VolumeUUID") or info.get("DiskUUID") or "").upper() or None
        if shutil.which("findmnt"):
            out = subprocess.run(["findmnt", "-n", "-o", "UUID", "-T", mp], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL).stdout.strip()
            return out.upper() or None
    except (OSError, subprocess.SubprocessError, ValueError, plistlib.InvalidFileException):
        return None
    return None


def mounted_volumes(roots=("/Volumes",)):
    """[(mount point, volume UUID)] of every volume mounted under /Volumes (a Claude session: ~/mnt as well), found by UUID, never by name."""
    out = []
    cand = []
    for r in roots + (os.path.expanduser("~/mnt"),):
        try:
            cand += [os.path.join(r, n) for n in sorted(os.listdir(r))]
        except OSError:
            pass
    for mp in cand:
        if os.path.isdir(mp) and os.path.ismount(mp):
            out.append((mp, volume_id(mp)))
    return out


def mount_of(vid):
    """Mount points of the volumes with this UUID (normally one; a disk and a copy of its image mounted together give two)."""
    return [mp for mp, v in mounted_volumes() if v and v == (vid or "").upper()]


def ranked(roles, connected=()):
    """[dict(role, label, size_gb, connected)] for these drive keys, the biggest drive first (then by name): how every list of "the drives that hold it"
    is shown. A key not in the registry keeps itself as its name. `connected` = the keys of the drives connected right now."""
    reg = {d["role"]: d for d in drives()}
    out = [dict(role=r, label=(reg.get(r) or {}).get("label") or r, size_gb=(reg.get(r) or {}).get("size_gb") or 0, connected=r in connected) for r in roles]
    return sorted(out, key=lambda x: (-x["size_gb"], x["label"]))


def lookup(vid):
    """The registered drive whose whole volume this is (a folder on the volume is a different entry: see folders)."""
    return next((d for d in drives() if d["id"] == vid and not d.get("folder")), None) if vid else None


def folders():
    """The registered drives that are a folder on a volume."""
    return [d for d in drives() if d.get("folder")]


def folder_of(path):
    """The registered folder drive whose TeslaCam folder this path is (or lies in), or None."""
    rp = os.path.realpath(os.path.expanduser(path))
    for d in folders():
        for mp in mount_of(d["id"]):
            top = os.path.realpath(os.path.join(mp, d["folder"]))
            if rp == top or rp.startswith(top + os.sep):
                return d
    return None


def folder_entry_for(path):
    """(volume UUID, folder path from the volume's root) of a folder: what a folder drive is registered by."""
    rp = os.path.realpath(os.path.expanduser(path))
    mp = _mount_point(rp)
    return volume_id(mp), os.path.relpath(rp, mp).replace(os.sep, "/")


def _free_role(label, function, taken):
    cands = ["A"] if function == "archive" else []
    cands += [c for c in (re.sub(r"[^A-Za-z]", "", label or "")[:1].upper(),) if c]
    cands += list("BCDEFGHIJKLMNOPQRSTUVWXYZ")
    return next(c for c in cands if c not in taken and c != "?")


def register(vid, label, function, size_gb=None, role=None, folder=None):
    """Remember a drive (or change what it is). Returns the entry. One word labels are kept as typed. `folder` = the TeslaCam folder from the volume's root,
    for a drive that is a folder on a volume."""
    if function not in FUNCTIONS:
        raise ValueError("function must be one of %s" % ", ".join(FUNCTIONS))
    label = (label or "").strip().split()[0] if (label or "").strip() else function.capitalize()
    cur = drives()
    same = lambda d: d["id"] == vid and (d.get("folder") or None) == (folder or None)
    old = next((d for d in cur if same(d)), None)
    taken = {d["role"] for d in cur if not same(d)}
    if size_gb is None and not (old or {}).get("size_gb"):         # not given and not known yet: measure the volume if it is mounted now
        mp = mount_of(vid)
        size_gb = _total_gb(mp[0]) if mp else None
    entry = dict(id=vid, label=label, function=function, role=role or (old["role"] if old else _free_role(label, function, taken)),
                 size_gb=size_gb if size_gb is not None else (old or {}).get("size_gb"), first_seen=(old or {}).get("first_seen") or datetime.date.today().isoformat())
    if folder:
        entry["folder"] = folder.strip("/")
    _write([d for d in cur if not same(d)] + [entry])
    return entry


def forget(vid, folder=None):
    cur = drives()
    _write([d for d in cur if not (d["id"] == vid and (d.get("folder") or None) == (folder or None))])
    return len(cur) != len(drives())


def _total_gb(path):
    try:
        return round(shutil.disk_usage(drive_root(path)).total / 1024 ** 3)
    except OSError:
        return None


def size_gb(path):
    return _total_gb(path)
