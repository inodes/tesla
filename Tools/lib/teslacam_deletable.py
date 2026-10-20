"""teslacam_deletable.py - which video on the connected TeslaCam drives is safe to delete, and what is still missing for the rest.

A video minute (all its cameras) may go only when what the app keeps from it exists:

  telemetry    the minute's speed / GPS / FSD readings are in <data>/teslacam_timeline
  stills       its front-camera stills (60 of them) are in <data>/teslacam_stills
  synced       the minute is not newer than the last drive synced from Tessie (later video has no drive folder to keep snapshots in yet)
  snapshots    when the minute lies inside a drive, that drive's snapshots/ folder has been built (the large one-second stills can only be
               cut from the video, so this has to happen before the video goes)

and a copy on JOWUA or the Tesla USB may go as soon as the Archive has the same file (same name, same size), whatever else is missing.

Only the drives are listed and the kept files looked at; nothing is opened, changed or deleted here.
"""
import bisect
import json
import os

import clip_clock as CC
import drive_fuse as DF
import drive_roles as DR
import teslacam_files as TF
import teslacam_stills as ST
import teslacam_timeline as TL

KEPT_MIN = 55                # a clip's stills are complete from this many (a clip is ~59-60 s)


def drive_windows(data):
    """[(utc start, utc end, has snapshots)] of every drive folder, sorted."""
    out = []
    base = os.path.join(data, "drives", "deepdive")
    for day in os.listdir(base) if os.path.isdir(base) else []:
        dd = os.path.join(base, day)
        for name in os.listdir(dd) if os.path.isdir(dd) else []:
            sj = os.path.join(dd, name, "drive_summary.json")
            try:
                with open(sj, encoding="utf-8") as fh:
                    sm = json.load(fh)
                a, b = DF.utc(DF._t(sm["start"], "%Y-%m-%d %H:%M:%S")), DF.utc(DF._t(sm["end"], "%Y-%m-%d %H:%M:%S"))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            out.append((a, b, os.path.isfile(os.path.join(dd, name, "snapshots", "index.json"))))
    out.sort()
    return out


def _snap_ok(windows, starts, t):
    """True when minute at UTC t is in no drive, or in a drive whose snapshots are built."""
    k = bisect.bisect_right(starts, t + 60) - 1
    while k >= 0 and windows[k][1] >= t - 60:               # every drive that overlaps this minute
        if windows[k][0] <= t + 60 and not windows[k][2]:
            return False
        k -= 1
    return True


def report(data, roots, front_only=True):
    """{role: dict(files, minutes, ready, no_telemetry, no_stills, no_snapshots, not_on_archive, copy_on_archive, examples)}.
    Counted per minute (a stamp), which is what is deleted together; `files` counts every camera file."""
    CC.configure(data)
    store = TL.Store(os.path.join(data, "teslacam_timeline"))
    stills = ST.Stills(data)
    windows = drive_windows(data)
    starts = [w[0] for w in windows]
    synced_until = max([w[1] for w in windows] or [0])         # drives are synced from Tessie up to here; later video has no drive yet
    listed = {}
    for role, root in roots.items():
        files, ok = TF.walk(root)
        listed[role] = (files, ok)
    arch = {(f["src"], f["ev"], f["stamp"], f["cam"]): f["size"] for f in listed.get("A", ([], False))[0]}
    kept_cache = {}
    out = {}
    for role, (files, ok) in listed.items():
        r = dict(files=len(files), minutes=0, ready=0, no_telemetry=0, no_stills=0, no_snapshots=0, not_synced=0, not_on_archive=0, copy_on_archive=0, examples={}, listed_ok=ok)
        by_min = {}
        for f in files:
            by_min.setdefault((f["src"], f["ev"], f["stamp"]), []).append(f)
        for (src, ev, stamp), fs in by_min.items():
            r["minutes"] += 1
            if role != "A":                                  # a copy elsewhere: safe once the Archive has every file of the minute, same size
                same = all(arch.get((f["src"], f["ev"], f["stamp"], f["cam"])) == f["size"] for f in fs)
                if same:
                    r["copy_on_archive"] += 1
                    continue
                r["not_on_archive"] += 1
                r["examples"].setdefault("not_on_archive", []).append(stamp)
                continue
            if stamp not in kept_cache:
                kept_cache[stamp] = (store.summary(stamp) is not None, stills.count(stamp) >= KEPT_MIN)
            has_tel, has_stills = kept_cache[stamp]
            if not has_tel:
                r["no_telemetry"] += 1
                r["examples"].setdefault("no_telemetry", []).append(stamp)
            if not has_stills:
                r["no_stills"] += 1
                r["examples"].setdefault("no_stills", []).append(stamp)
            snap = _snap_ok(windows, starts, CC.utc(stamp))
            if not snap:
                r["no_snapshots"] += 1
                r["examples"].setdefault("no_snapshots", []).append(stamp)
            late = CC.utc(stamp) > synced_until
            if late:
                r["not_synced"] += 1
                r["examples"].setdefault("not_synced", []).append(stamp)
            if has_tel and has_stills and snap and not late:
                r["ready"] += 1
        out[role] = r
    return out
