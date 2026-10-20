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
import subprocess

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


def _add(r, detail, root, src, stamp, fs, safe):
    if detail:
        r["detail"].append(dict(stamp=stamp, src=src, safe=safe, bytes=sum(f["size"] for f in fs), paths=[TF.front_path(root, f) for f in fs]))


class _Durations:
    """Length in seconds of front clips, asked of ffprobe once and kept in <data>/teslacam_durations.json (a clip never changes)."""

    def __init__(self, data):
        self.path = os.path.join(data, "teslacam_durations.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.d = json.load(fh)
        except (OSError, ValueError):
            self.d = {}
        self.new = 0

    def get(self, stamp, video):
        if stamp not in self.d:
            try:
                out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", video],
                                     capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).stdout.strip()
                self.d[stamp] = float(out)
                self.new += 1
            except (OSError, ValueError, subprocess.SubprocessError):
                return None
        return self.d[stamp]

    def save(self):
        if self.new:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.d, fh)
            os.replace(tmp, self.path)


def stills_complete(stills, durations, stamp, video):
    """A clip's stills are complete at KEPT_MIN images, or fewer when the clip itself is short (a second or two of slack)."""
    n = stills.count(stamp)
    if n >= KEPT_MIN:
        return True
    if n == 0:
        return False
    d = durations.get(stamp, video) if video else None
    return d is not None and n >= int(d) - 2


REASONS = ("not_synced", "no_telemetry", "no_stills", "no_snapshots")          # a minute that is not ready is counted under the first that applies


def report(data, roots, detail=False):
    """{role: dict(files, minutes, bytes, ready, ready_bytes, ready_kinds, blocked, drives_without_snapshots, copy_on_archive, not_on_archive, examples)}.
    Counted per minute (a stamp), which is what is deleted together; `files` counts every camera file.
    On the Archive every minute is in exactly one place: ready, or the first thing it waits for (REASONS), so the numbers add up to `minutes`.
    blocked[reason] = dict(minutes, bytes, first, last day). Telemetry is only expected of Recent and Saved clips (a parked Sentry clip has none)
    and only of a minute with a front camera. With detail, r["detail"] lists every minute: dict(stamp, src, safe, bytes, paths)."""
    CC.configure(data)
    store = TL.Store(os.path.join(data, "teslacam_timeline"))
    stills = ST.Stills(data)
    durations = _Durations(data)
    windows = drive_windows(data)
    starts = [w[0] for w in windows]
    synced_until = max([w[1] for w in windows] or [0])         # drives are synced from Tessie up to here; later video has no drive yet
    listed = {}
    for role, root in roots.items():
        files, ok = TF.walk(root)
        listed[role] = (files, ok)
    arch = {(f["src"], f["ev"], f["stamp"], f["cam"]): f["size"] for f in listed.get("A", ([], False))[0]}
    out = {}
    for role, (files, ok) in listed.items():
        r = dict(files=len(files), minutes=0, bytes=sum(f["size"] for f in files), ready=0, ready_bytes=0, ready_kinds={}, blocked={}, not_on_archive=0, copy_on_archive=0,
                 examples={}, listed_ok=ok, detail=[], drives_without_snapshots=sum(1 for w in windows if not w[2]))
        by_min = {}
        for f in files:
            by_min.setdefault((f["src"], f["ev"], f["stamp"]), []).append(f)
        for (src, ev, stamp), fs in by_min.items():
            r["minutes"] += 1
            size = sum(f["size"] for f in fs)
            if role != "A":                                  # a copy elsewhere: safe once the Archive has every file of the minute, same size
                same = all(arch.get((f["src"], f["ev"], f["stamp"], f["cam"])) == f["size"] for f in fs)
                r["copy_on_archive" if same else "not_on_archive"] += 1
                if not same:
                    r["examples"].setdefault("not_on_archive", []).append(stamp)
                _add(r, detail, roots[role], src, stamp, fs, same)
                continue
            front = next((f for f in fs if f["cam"] == "front"), None)
            video = TF.front_path(roots[role], front) if front else None
            reason = None
            if CC.utc(stamp) > synced_until:
                reason = "not_synced"
            elif front and src in ("recent", "saved") and store.summary(stamp) is None:
                reason = "no_telemetry"
            elif front and not stills_complete(stills, durations, stamp, video):
                reason = "no_stills"
            elif not _snap_ok(windows, starts, CC.utc(stamp)):
                reason = "no_snapshots"
            if reason:
                b = r["blocked"].setdefault(reason, dict(minutes=0, bytes=0, first=stamp[:10], last=stamp[:10]))
                b["minutes"] += 1
                b["bytes"] += size
                b["first"], b["last"] = min(b["first"], stamp[:10]), max(b["last"], stamp[:10])
                r["examples"].setdefault(reason, []).append(stamp)
            else:
                r["ready"] += 1
                r["ready_bytes"] += size
                k = r["ready_kinds"].setdefault(src, dict(minutes=0, bytes=0))
                k["minutes"] += 1
                k["bytes"] += size
            _add(r, detail, roots[role], src, stamp, fs, reason is None)
        out[role] = r
    durations.save()
    return out
