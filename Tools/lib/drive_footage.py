"""drive_footage.py - one drive's data for its page and its GPS path: drives_master.csv (load_master, find_master), the drive's Tessie CSV (load_drive_csv) and window, and the TeslaCam footage that covers it (Footage). Used by drive_report and drive_gps."""

import bisect
import csv
import datetime
import json
import os
import subprocess
import tempfile
import drive_points as dpts
from lib import dashcam_clips as D

OFFSETS = D.OFFSETS
NOFOOT = 10 ** 9      # an offset no clip can match: the footage found for a drive was checked against its route and is not from it


def f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def pdt(s):
    return datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------- inputs
def load_master(data):
    """Sorted [(local start, local end, row)] - local times from the UTC epochs (Australia/Sydney), NOT from the
    'Started At (Local)' text: that text carries a stale AEST/AEDT label on some days after daylight saving starts."""
    from zoneinfo import ZoneInfo
    z = ZoneInfo("Australia/Sydney")
    out = []
    p = os.path.join(data, "drives", "drives_master.csv")
    if not os.path.exists(p):
        return out
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            a = datetime.datetime.fromtimestamp(int(r["Started At (UTC)"]), z).replace(tzinfo=None)
            b = datetime.datetime.fromtimestamp(int(r["Ended At (UTC)"]), z).replace(tzinfo=None)
        except Exception:
            continue
        out.append((a, b, r))
    out.sort(key=lambda x: x[0])
    return out


def find_master(master, t):
    """Master row whose local start is nearest to time t (within 6 min), else None."""
    if not master:
        return None
    ks = [m[0] for m in master]
    i = bisect.bisect_left(ks, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(master):
            d = abs((master[j][0] - t).total_seconds())
            if d <= 360 and (best is None or d < best[0]):
                best = (d, master[j])
    return best[1] if best else None


def load_drive_csv(path):
    rows = dpts.table(path)                       # JSON or CSV alike (lib/drive_points.py)
    if len(rows) < 2:
        return None
    h = rows[0]
    ix = {n: i for i, n in enumerate(h)}

    def col(name):
        return ix.get(name)

    c_t, c_la, c_lo, c_v, c_p, c_el, c_b, c_od, c_sh = (0, col("Latitude"), col("Longitude"), col("Speed (km/h)"), col("Power (kW)"),
                                                          col("Elevation (m)"), col("Battery Level (%)"), col("Odometer (km)"), col("Shift State"))
    c_ci, c_cv = col("Pack Current (A)"), col("Pack Voltage (V)")
    c_rg = col("Battery Range (km)") if col("Battery Range (km)") is not None else col("Est. Battery Range (km)")
    c_it, c_ot = col("Inside Temp (°C)"), col("Outside Temp (°C)")
    pts = []
    for r in rows[1:]:
        try:
            t = pdt(r[c_t])
        except Exception:
            continue
        g = lambda c: f(r[c]) if c is not None and c < len(r) else None
        pw = g(c_p)
        if pw is None and g(c_ci) is not None and g(c_cv) is not None:
            pw = -g(c_ci) * g(c_cv) / 1000.0        # Tessie leaves Power empty in some exports: pack current is negative when discharging
        pts.append(dict(t=t, la=g(c_la), lo=g(c_lo), v=g(c_v), p=pw, el=g(c_el), b=g(c_b), od=g(c_od), rg=g(c_rg), it=g(c_it), ot=g(c_ot), sh=(r[c_sh] if c_sh is not None and c_sh < len(r) else "")))
    return pts


def drive_window(pts, m):
    """Rows that belong to the drive: from the master start/end when known, else first/last moving row."""
    if m:
        a, b = m[0], m[1] + datetime.timedelta(seconds=59)
        sel = [p for p in pts if a - datetime.timedelta(seconds=45) <= p["t"] <= b + datetime.timedelta(seconds=45)]
        if len(sel) >= 3:
            return sel
    mv = [i for i, p in enumerate(pts) if (p["v"] or 0) > 0]
    if not mv:
        return pts
    return pts[max(0, mv[0] - 1): mv[-1] + 2]


# ----------------------------------------------------------------------------- footage
class Footage:
    """RecentClips + SavedClips + SentryClips of every TeslaCam folder as one list of front clips (clip clock)."""

    def __init__(self, roots, since):
        self.roots = roots
        self.clips = D.find_clips(roots, since) if roots else []
        self.starts = [c[0] for c in self.clips]
        self._series = {}
        self.verified = []   # (drive start, offset) where the dashcam GPS settled the clock offset
        self.how = ""        # how the last best_offset was decided: gps / neighbour / count
        self.saved = []     # (folder path, event dict) for SavedClips/*
        for r in roots:
            for sub in ("SavedClips",):
                try:
                    for e in os.scandir(os.path.join(r, sub)):
                        if e.is_dir() and e.name[:10] >= since:
                            try:
                                ev = json.load(open(os.path.join(e.path, "event.json")))
                                self.saved.append((e.path, ev))
                            except Exception:
                                pass
                except OSError:
                    pass

    def in_window(self, a, b, off):
        """Clips whose minute overlaps [a, b] (Tessie local) given the clip-clock offset in seconds."""
        i = bisect.bisect_left(self.starts, a - datetime.timedelta(seconds=off + 60))
        out = []
        while i < len(self.starts) and self.starts[i] + datetime.timedelta(seconds=off) <= b:
            out.append(self.clips[i])
            i += 1
        return out

    def _fix(self, path, frac):
        """(lat, lon, seconds into the clip) of one moving GPS fix from a clip, or None."""
        S = self._series.get(path)
        if S is None:
            try:
                S = self._series[path] = D.read_series(path)
            except Exception:
                S = self._series[path] = {"n": 0}
        n = S.get("n") or 0
        if not n:
            return None
        i0 = min(n - 1, int(n * frac))
        for i in list(range(i0, n)) + list(range(i0 - 1, -1, -1)):
            if S["lat"][i] and S["lon"][i] and (S["v"][i] or 0) > 5:
                return S["lat"][i], S["lon"][i], (S.get("dur") or 60.0) * i / n
        return None

    def gps_error(self, a, b, off, route, samples=5):
        """Median distance (km) between where the dashcam says the car was and where Tessie says it was at that moment, for the clip clock offset off."""
        clips = self.in_window(a, b, off)
        if not clips or not route:
            return None
        ts = [p["t"] for p in route]
        errs = []
        for k in range(samples):
            c = clips[min(len(clips) - 1, int((k + 0.5) * len(clips) / samples))]
            fx = self._fix(c[1], 0.5)
            if not fx:
                continue
            t = c[0] + datetime.timedelta(seconds=off + fx[2])
            j = bisect.bisect_left(ts, t)
            near = [route[x] for x in (j - 1, j) if 0 <= x < len(route) and route[x]["la"] is not None]
            if not near:
                continue
            # between two Tessie samples the car is on the line joining them: use the closer of the two ends and the nearest point of that segment
            e = min(D.hav_km(fx[0], fx[1], p["la"], p["lo"]) for p in near)
            errs.append(e)
        return sorted(errs)[len(errs) // 2] if len(errs) >= 2 else None

    def best_offset(self, a, b, route=None):
        """(offset seconds, clips in window). The clip clock is sometimes an hour behind Tessie (the car's clock stays on the old time zone),
        so when several offsets find footage the dashcam's own GPS decides which one puts the car where Tessie says it was."""
        found = [(off, len(self.in_window(a, b, off))) for off in OFFSETS]
        found = [x for x in found if x[1] > 0]
        self.how = "count"
        if route and len(found) > 1:
            scored = [(self.gps_error(a, b, off, route), off, n) for off, n in found]
            scored = [x for x in scored if x[0] is not None]
            if scored:
                e, off, n = min(scored)
                if e < 3.0:
                    self.how = "gps"
                    self.verified.append((a, off))
                    return off, n
        # the car's clock changes in steps (it stayed an hour behind Tessie for a week), so a drive the GPS cannot settle
        # (short, parked, no moving fixes) takes the offset of the nearest drive in time that it did settle
        if self.verified and len(found) > 0:
            t, off = min(self.verified, key=lambda v: abs((v[0] - a).total_seconds()))
            n = len(self.in_window(a, b, off))
            if abs((t - a).total_seconds()) < 36 * 3600 and n > 0:
                self.how = "neighbour"
                return off, n
        best = (0, 0)
        for off in OFFSETS:
            n = len(self.in_window(a, b, off))
            if n > best[1]:
                best = (off, n)
        # footage at a single offset is still checked against the route: clips that sit kilometres from where Tessie says the car was are another drive's
        if route and best[1] and self.how == "count":
            e = self.gps_error(a, b, best[0], route)
            if e is not None and e > 5.0:
                self.how = "mismatch"
                return NOFOOT, 0
        return best

    def cut(self, center, before, after, off, out_mp4, paths_pool=None):
        """Re-encode a small front-camera snippet [center-before, center+after] (Tessie local) to out_mp4."""
        a = center - datetime.timedelta(seconds=before + off)
        b = center + datetime.timedelta(seconds=after - off)
        pool = paths_pool if paths_pool is not None else self.clips
        pst = [c[0] for c in pool]
        i = max(0, bisect.bisect_right(pst, a) - 1)
        segs = []
        while i < len(pool) and pool[i][0] < b:
            s, path = pool[i]
            lo = max(0.0, (a - s).total_seconds())
            hi = min(60.0, (b - s).total_seconds())
            if hi - lo > 0.3 and (a - s).total_seconds() < 62:
                segs.append((path, lo, hi))
            i += 1
        if not segs:
            return False
        lst = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        for path, lo, hi in segs:
            lst.write("file '%s'\ninpoint %.2f\noutpoint %.2f\n" % (path.replace("'", r"'\''"), lo, hi))
        lst.close()
        tmp = out_mp4 + ".tmp.mp4"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-vf", "scale=960:-2", "-an",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-movflags", "+faststart", tmp],
                           capture_output=True, stdin=subprocess.DEVNULL)
        os.unlink(lst.name)
        if r.returncode != 0 or not os.path.exists(tmp):
            return False
        os.replace(tmp, out_mp4)
        return True


def default_data():
    return D.DEFAULT_DATA
