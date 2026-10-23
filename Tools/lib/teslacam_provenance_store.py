"""teslacam_provenance_store.py - where each clip came from and what the car itself said about it, kept beside (never inside) the other stores.

For every clip it has seen, one record per RECORDING (not per file name):

    created / modified   UTC epoch seconds, from the MP4 header (teslacam_provenance.read_header): the car's own start and close time
    name_stamp           the clip's file-name reading of the car's clock (a label only; never used as a time)
    offset               name_stamp - created rounded to 15 minutes: how far the car's clock was from UTC when it wrote the file (changes when the car's clock changes); skew_s = unrounded
    street / suburb      what the car wrote about where it was
    size, tracks_agree   size at first sight; whether the header's three time fields agree
    learned_utc / agree  what the learned clock (clip_clock.py) says for the name, and whether it matches the header within 2 s
    clock_ver, tz_db     the version of the learned clock and of the time-zone database used when this was recorded
    sources              {role: {rel, size, read}}: each drive that holds it, its path under TeslaCam, its size there, when it was read
    flags                'collision' (the same name, a different recording), 'size_differs' (the same recording, a different size on another drive)

A clip is identified by name + header creation time (within 2 s: the name truncates the second). The same name with a different creation time is a
DIFFERENT recording (the car's clock went back and wrote an existing name again): both are kept, flagged, and nothing is overwritten. Keys are the footage log's
keys ('recent||<stamp>-front', 'saved|<event>|<stamp>-front', 'sentry|<event>|<stamp>-front'), one file per name day: <data>/teslacam_provenance/<day>.json
{key: [record, ...]}. Only headers are read (about 1 ms each); no video is opened, moved or changed.

    Store.observe(key, path, role)       read one clip's header and record it (returns the record)
    backfill(store, ledger, roots, ...)  observe every front-camera clip the footage log lists on a connected drive, skipping what is recorded
    clock_events(store)                  [dict(utc, from_h, to_h, name)] every change of the car's clock offset, in UTC order
    summary(store)                       counts, collisions, offsets, disagreements with the learned clock
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import teslacam_provenance as P

DIRNAME = "teslacam_provenance"
SAME = 2                    # seconds: the name truncates the second, so the same recording can differ by one
_KIND_DIR = {"sentry": "SentryClips", "saved": "SavedClips"}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def key_rel(key):
    """Path of the file named by a footage-log key, under a drive's TeslaCam folder."""
    src, ev, name = key.split("|", 2)
    if src == "recent":
        return os.path.join("RecentClips", name + ".mp4")
    return os.path.join(_KIND_DIR[src], ev, name + ".mp4")


def tz_db():
    try:
        import tzdata
        return "tzdata " + tzdata.IANA_VERSION
    except Exception:
        return "system"


class Store:
    def __init__(self, data_dir, clock=None):
        self.root = os.path.join(data_dir, DIRNAME)
        self._days = {}
        self._lock = threading.RLock()
        self._dirty = set()
        self.clock = clock          # the clip_clock module (or anything with utc(stamp) and version()); None = not compared
        self._tz = tz_db()

    def _path(self, day):
        return os.path.join(self.root, day + ".json")

    def day(self, day):
        with self._lock:
            d = self._days.get(day)
            if d is None:
                try:
                    with open(self._path(day), encoding="utf-8") as f:
                        d = json.load(f)
                except (OSError, ValueError):
                    d = {}
                self._days[day] = d
            return d

    def days(self):
        try:
            on_disk = {n[:-5] for n in os.listdir(self.root) if len(n) == 15 and n.endswith(".json")}
        except OSError:
            on_disk = set()
        with self._lock:
            return sorted(on_disk | {d for d, v in self._days.items() if v})

    def flush(self):
        with self._lock:
            os.makedirs(self.root, exist_ok=True)
            for day in sorted(self._dirty):
                p = self._path(day)
                with open(p + ".part", "w", encoding="utf-8") as f:
                    json.dump(self._days[day], f, separators=(",", ":"), sort_keys=True)
                os.replace(p + ".part", p)
            self._dirty.clear()

    def records(self, key):
        return self.day(key.split("|")[2][:10]).get(key, [])

    def has(self, key, role):
        return any(role in r["sources"] for r in self.records(key))

    # ---- recording one clip ---------------------------------------------------------------------------------------
    def observe(self, key, path, role, header=None):
        """Record the clip at `path` (held by drive `role`). Returns the record, or None when the header could not be read."""
        h = header or P.read_header(path)
        if h["created"] is None:
            return None
        name = os.path.basename(path)
        stamp = key.split("|", 2)[2].rsplit("-", 1)[0] if key.count("|") == 2 else name[:19]
        ns = P.name_stamp(name)
        rel = key_rel(key)
        with self._lock:
            day = key.split("|")[2][:10]
            recs = self.day(day).setdefault(key, [])
            rec = next((r for r in recs if abs(r["created"] - h["created"]) <= SAME), None)
            if rec is None:
                raw = (ns - h["created"]) if ns is not None else None      # the name truncates the second and a clip can start a little after its name: the clock offset is a multiple of 15 minutes
                rec = dict(created=h["created"], modified=h["modified"], name_stamp=ns, offset=None if raw is None else int(round(raw / 900.0)) * 900, skew_s=raw, street=h["street"],
                           suburb=h["suburb"], size=h["size"], tracks_agree=h["tracks_agree"], flags=[], sources={}, tz_db=self._tz)
                if recs:
                    rec["flags"].append("collision")
                    for r in recs:
                        if "collision" not in r["flags"]:
                            r["flags"].append("collision")
                if self.clock:
                    try:
                        lu = self.clock.utc(stamp)
                        rec.update(learned_utc=lu, agree=abs(lu - h["created"]) <= SAME, clock_ver=self.clock.version())
                    except Exception:
                        pass
                recs.append(rec)
            elif h["size"] != rec["size"] and "size_differs" not in rec["flags"]:
                rec["flags"].append("size_differs")
            rec["sources"][role] = dict(rel=rel, size=h["size"], read=now_iso())
            self._dirty.add(day)
            return rec


def backfill(store, ledger, roots, days=None, cams=("front",), stop=None, progress=None, workers=4, flush_every=500):
    """Observe every clip (only the cameras in `cams`) that the footage log lists on a connected drive and that is not recorded yet.
    roots = {role: that drive's TeslaCam folder}. Returns dict(read, skipped, failed, days)."""
    todo = []
    for day in (days or ledger.days()):
        for key, rec in ledger.day(day).items():
            if key.count("|") != 2 or not any(key.endswith("-" + c) for c in cams):
                continue
            for role, d in (rec.get("drives") or {}).items():
                if not d.get("gone") and roots.get(role) and not store.has(key, role):
                    todo.append((key, role))
    done = dict(read=0, skipped=0, failed=0, total=len(todo))
    lock = threading.Lock()

    def one(item):
        if stop and stop():
            return
        key, role = item
        path = os.path.join(roots[role], key_rel(key))
        h = P.read_header(path)
        with lock:
            if h["created"] is None:
                done["failed"] += 1
            else:
                store.observe(key, path, role, header=h)
                done["read"] += 1
            if progress:
                progress(done["read"] + done["failed"], done["total"])
            if (done["read"] + done["failed"]) % flush_every == 0:
                store.flush()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))
    store.flush()
    return done


def all_records(store):
    for day in store.days():
        for key, recs in store.day(day).items():
            for r in recs:
                yield key, r


def clock_events(store):
    """Every change of the car's clock offset, in UTC order: [dict(utc, from_h, to_h, name)]. Offsets of one hour apart only (a recording gap is not a change)."""
    rows = sorted((r["created"], r["offset"], k) for k, r in all_records(store) if r.get("offset") is not None)
    out, prev = [], None
    for created, off, key in rows:
        if prev is not None and off != prev:
            out.append(dict(utc=created, from_h=prev / 3600, to_h=off / 3600, name=key.split("|")[2]))
        prev = off
    return out


def summary(store):
    n = 0
    offs, flags, bad, nosee = {}, {}, 0, 0
    for k, r in all_records(store):
        n += 1
        offs[r.get("offset")] = offs.get(r.get("offset"), 0) + 1
        for f in r["flags"]:
            flags[f] = flags.get(f, 0) + 1
        if r.get("agree") is False:
            bad += 1
        if r.get("tracks_agree") is False:
            nosee += 1
    return dict(records=n, offsets={(o / 3600 if o is not None else None): c for o, c in offs.items()}, flags=flags, disagree_with_learned_clock=bad, tracks_disagree=nosee,
                clock_events=len(clock_events(store)))
