#!/usr/bin/env python3
"""drive_report.py - one summary page per drive, written into that drive's own folder (REQ-193).

    <data>/drives/deepdive/<stamp>/drive.html     self-contained page: stats, route map, speed/power/elevation graph,
                                                  saved-clip events, dashcam coverage
    <data>/drives/deepdive/<stamp>/clips/         only when RecentClips / SavedClips footage overlaps the drive:
                                                  E###.jpg (1 s stills), E###.mp4 (short front snippet),
                                                  saved_<folder>.mp4 (front snippet around the saved event)
    <data>/drives/deepdive/index.html             every drive on one page (day by day, filterable)

Usage:
  drive_report.py [--data-dir DIR] [--teslacam DIR ...] [--since YYYY-MM-DD] [--only STAMP[,STAMP]]
                  [--no-video] [--force] [--budget SECONDS]
  --since     only (re)build drives starting on/after this date (default: all)
  --only      comma list of drive stamps, e.g. 2026-10-11_15-45_AEDT
  --no-video  write pages and stills but skip the mp4 snippets
  --force     rebuild pages that are already up to date (default skips a page that is newer than its CSV and this script, and has no new footage)
  --budget    stop starting new drives after this many seconds (use ~85 on the device bridge, then run again)

Data used: the drive's own Tessie CSV (route, speed, power, elevation, battery), drives_master.csv (totals), the FSD
flip analysis (analyse_fsd_osm_limits.py -> fsd_transitions.csv + dashcam_flip_review/), and TeslaCam footage when it
exists. Tessie has no FSD state; where footage exists, the flip rows show the car's own state from the clip.
Footage only exists for drives since the last time the car kept RecentClips (24 h rolling when not synced).
"""
import argparse
import bisect
import csv
import datetime
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_paths as dp  # noqa: E402
import clip_clock as CC  # noqa: E402
import dashcam_flip_review as D  # noqa: E402
import footage_ui as FU  # noqa: E402
import drive_roles as DR  # noqa: E402
import drive_charts as DC  # noqa: E402
import app_config as CFG  # noqa: E402
import drive_map_ui as MU  # noqa: E402

REASONS = D.REASONS
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


PLACES = []     # [(name, lat, lon, radius_m)] from places.json, the user's own place registry


def load_places(data):
    out = []
    p = os.path.join(data, "places.json")
    try:
        for name, v in json.load(open(p, encoding="utf-8")).items():
            if isinstance(v, dict) and v.get("lat") is not None and v.get("lon") is not None:
                out.append((name, float(v["lat"]), float(v["lon"]), float(v.get("radius_m") or 100)))
    except Exception:
        pass
    return out


def place_name(m, which):
    """Name for the start/end of a drive: places.json (inside a place's radius; the closest relative to its radius wins),
    else Tessie's saved-location label, else the first part of the street address."""
    if not m:
        return ""
    la, lo = f(m.get(which + " Latitude")), f(m.get(which + " Longitude"))
    if la is not None and lo is not None:
        best = None
        for name, pla, plo, r in PLACES:
            d = D.hav_km(la, lo, pla, plo) * 1000
            if d <= r and (best is None or d / r < best[0]):
                best = (d / r, name)
        if best:
            return best[1]
    return m.get(which + " Saved Location") or (m.get(which + " Location") or "").split(",")[0]


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


def load_flips(data):
    """{csv basename: [flip rows with id E###]} - id is the row number in fsd_transitions.csv, as the dashcam review uses."""
    p = os.path.join(data, "drives", "analysis", "fsd_transitions.csv")
    out = {}
    if not os.path.exists(p):
        return out
    for i, r in enumerate(csv.DictReader(open(p, encoding="utf-8")), 1):
        r["id"] = "E%03d" % i
        r["flip"] = r.get("kind", "")
        out.setdefault(r["file"], []).append(r)
    return out


def load_recs(data):
    out = {}
    d = os.path.join(data, "drives", "analysis", "dashcam_flip_review", "cache7")
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.startswith("E") and fn.endswith(".json"):
                try:
                    r = json.load(open(os.path.join(d, fn)))
                    if "clip" in r:
                        out[r["id"]] = r
                except Exception:
                    pass
    return out


def load_drive_csv(path):
    rows = list(csv.reader(open(path, encoding="utf-8")))
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


def gps_mtime(folder, stamp):
    try:
        return os.path.getmtime(os.path.join(dp.drives_dir(folder), "deepdive_gps", stamp, "drive_gps.json"))
    except OSError:
        return 0


def load_gps(folder, stamp, a, b):
    """The dashcam path of drive_gps.py (drives/deepdive_gps/<stamp>/), only when it was judged to agree with Tessie.
    -> (list of [epoch, lat, lon, km/h, autopilot], info dict) or (None, None)."""
    d = os.path.join(dp.drives_dir(folder), "deepdive_gps", stamp)
    try:
        info = json.load(open(os.path.join(d, "drive_gps.json")))
        if info.get("verdict") != "used":
            return None, None
        out = []
        for r in csv.DictReader(open(os.path.join(d, "drive_deepdive_%s.csv" % stamp), encoding="utf-8")):
            if r.get("Source") != "dashcam":
                continue
            t = pdt(r[[k for k in r if k.startswith("Timestamp")][0]])
            if a - datetime.timedelta(seconds=5) <= t <= b + datetime.timedelta(seconds=5):
                out.append([(t - datetime.datetime(1970, 1, 1)).total_seconds(), round(float(r["Latitude"]), 6), round(float(r["Longitude"]), 6), f(r["Speed (km/h)"], 0), r.get("Autopilot State", "")])
        return (out, info) if len(out) > 5 else (None, None)
    except (OSError, ValueError, KeyError, IndexError):
        return None, None


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


CAM_FILES = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")


def clip_ref(path, roots):
    """(volume name, rel path without camera suffix, cameras present as digits, role) for a front-camera file.
    The role (A Archive / J JOWUA / U Tesla USB) comes from what is on the drive (lib/drive_roles.py), never from the
    volume name, which changes with mount order. The Archive copy is preferred when the same minute also exists there."""
    parts = path.split(os.sep)
    if "TeslaCam" not in parts or not path.endswith("-front.mp4"):
        return None
    i = len(parts) - 1 - parts[::-1].index("TeslaCam")
    rel = "/".join(parts[i + 1:])[:-len("-front.mp4")]
    best = (parts[i - 1], os.sep.join(parts[:i + 1]))
    for r in roots:
        if DR.role_of(r) == "A" and os.path.exists(os.path.join(r, rel + "-front.mp4")):
            best = (os.path.basename(os.path.dirname(r.rstrip(os.sep))), r)
            break
    base = os.path.basename(rel)
    folder = os.path.dirname(os.path.join(best[1], rel))
    cams = "".join(str(k) for k, c in enumerate(CAM_FILES) if os.path.exists(os.path.join(folder, base + "-" + c + ".mp4")))
    return best[0], rel, cams, DR.role_of(best[1])


def saved_pool(folder):
    out = {}
    try:
        for e in os.scandir(folder):
            n = e.name
            if n.endswith("-front.mp4") and not n.startswith("._"):
                try:
                    out[datetime.datetime.strptime(n[:19], "%Y-%m-%d_%H-%M-%S")] = e.path
                except ValueError:
                    pass
    except OSError:
        pass
    return sorted(out.items())


# ----------------------------------------------------------------------------- page
_HERE = os.path.dirname(os.path.abspath(__file__))
# a page older than this generator (or its chart / footage / map modules) is rebuilt on the next run
CODE_MTIME = max(os.path.getmtime(f) for f in [os.path.abspath(__file__)] + [os.path.join(_HERE, "lib", n) for n in ("drive_charts.py", "footage_ui.py", "drive_roles.py")] if os.path.exists(f))

CSS = r"""
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mut:#667085;--line:#e3e6ec;--acc:#0a6cff;--ok:#18a058;--warn:#e08a00;--bad:#d4332b;
--s0:#6b7a99;--s1:#2f7fd8;--s2:#18a058;--s3:#e0b000;--s4:#ee7a1a;--s5:#d4332b}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#171c23;--ink:#e8ecf2;--mut:#93a0b4;--line:#283040;--acc:#5aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:15px;margin:0 0 8px;color:var(--mut);font-weight:600;letter-spacing:.02em;text-transform:uppercase}
.sub{color:var(--mut);margin-bottom:16px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}.stat b{display:block;font-size:20px}.stat span{color:var(--mut);font-size:12px}
svg text{fill:var(--mut);font-size:11px}.grid line{stroke:var(--line)}
.legend{display:flex;gap:12px;flex-wrap:wrap;color:var(--mut);font-size:12px;margin-top:6px}.legend i{display:inline-block;width:14px;height:4px;border-radius:2px;vertical-align:middle;margin-right:4px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:600;font-size:12px}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}.bad{color:var(--bad)}.ok{color:var(--ok)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}video{max-width:100%;border-radius:8px;background:#000}
.ev{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;margin-bottom:14px}@media(max-width:760px){.ev{grid-template-columns:1fr}}
.cov{height:14px;border-radius:7px;background:var(--line);position:relative;overflow:hidden}.cov i{position:absolute;top:0;bottom:0;background:var(--ok)}
.basemap{filter:grayscale(1)}@media (prefers-color-scheme:dark){.basemap{filter:grayscale(1) invert(.92) brightness(.8)}}
.tip{position:fixed;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.2);display:none;z-index:5}
.sheet{width:100%;border-radius:8px;display:block}.two{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}.mapbtn{float:right;font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:2px 10px;margin-left:6px;cursor:pointer}
"""

JS = r"""
const D=DATA,$=s=>document.querySelector(s),NS="http://www.w3.org/2000/svg";
const f0=x=>x==null?"-":Math.round(x),fmtT=t=>{const d=new Date(t*1000);return d.toISOString().substr(11,5)};
/*SC_JS*/
const T=D.pts.map(p=>p[0]),N=T.length;
// ---- map
const staticMap=function(){const svg=$("#map"),W=1060,H=540,pad=26;let la=D.pts.map(p=>p[1]),lo=D.pts.map(p=>p[2]);
 const ok=la.map((v,i)=>v!=null&&lo[i]!=null);if(!ok.some(x=>x)){svg.outerHTML="<div class=sub>No GPS in this file.</div>";return}
 const L=la.filter((v,i)=>ok[i]),O=lo.filter((v,i)=>ok[i]);const a0=Math.min(...L),a1=Math.max(...L),o0=Math.min(...O),o1=Math.max(...O);
 const kx=Math.cos((a0+a1)/2*Math.PI/180),R=Math.PI/180;
 // Web Mercator, so a map-tile basemap lines up with the route
 const MX=o=>(o+180)/360,MY=a=>(1-Math.log(Math.tan(a*R)+1/Math.cos(a*R))/Math.PI)/2;
 const mx0=MX(o0),mx1=MX(o1),my0=MY(a1),my1=MY(a0),dx=Math.max(mx1-mx0,1e-9),dy=Math.max(my1-my0,1e-9),k=Math.min((W-2*pad)/dx,(H-2*pad)/dy),
  ox=pad+((W-2*pad)-dx*k)/2,oy=pad+((H-2*pad)-dy*k)/2,X=o=>ox+(MX(o)-mx0)*k,Y=a=>oy+(MY(a)-my0)*k;D.X=X;D.Y=Y;
 // faint grey basemap (Esri Light Gray Canvas - built on OpenStreetMap data, no key or Referer needed; OSM's own tile server blocks pages opened from a file)
 const zd0=(function(){try{return +localStorage.getItem("mapDetail")||0}catch(e){return 0}})();D.zd=zd0;
 const tg=document.createElementNS(NS,"g");tg.setAttribute("class","basemap");svg.appendChild(tg);
 const tiles=function(){tg.innerHTML="";const z0=Math.round(Math.log2(k/256))+1,xa=mx0+(0-ox)/k,xb=mx0+(W-ox)/k,ya=my0+(0-oy)/k,yb=my0+(H-oy)/k;let z=Math.max(3,Math.min(16,z0+D.zd)),n=2**z;
  while(z>3&&(Math.floor(xb*n)-Math.floor(xa*n)+1)*(Math.floor(yb*n)-Math.floor(ya*n)+1)>36){z--;n=2**z}   /* never more than ~36 tiles: each is a network fetch, which is what made the map slow */
  const sz=k/n;
  for(let tx=Math.floor(xa*n);tx<=Math.floor(xb*n);tx++)for(let ty=Math.floor(ya*n);ty<=Math.floor(yb*n);ty++){if(ty<0||ty>=n)continue;
   const im=document.createElementNS(NS,"image");im.setAttribute("decoding","async");im.setAttribute("href",`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/${z}/${ty}/${((tx%n)+n)%n}`);
   im.setAttribute("x",(ox+(tx/n-mx0)*k).toFixed(2));im.setAttribute("y",(oy+(ty/n-my0)*k).toFixed(2));im.setAttribute("width",(sz+.6).toFixed(2));im.setAttribute("height",(sz+.6).toFixed(2));tg.appendChild(im)}
  const zl=$("#zlab");if(zl)zl.textContent="zoom "+z};
 tiles();D.tiles=tiles;
 const bm=(d)=>{D.zd=Math.max(-3,Math.min(3,D.zd+d));try{localStorage.setItem("mapDetail",D.zd)}catch(e){}tiles()};
 const mb=$("#mapbtns");if(mb){mb.innerHTML='<button class="mapbtn" id="zp">Detail +</button><button class="mapbtn" id="zm">Detail −</button><span id="zlab" class="sub" style="float:right;margin-top:2px"></span>';$("#zp").onclick=()=>bm(1);$("#zm").onclick=()=>bm(-1);tiles()}
 const t=document.createElementNS(NS,"text");t.setAttribute("x",W-4);t.setAttribute("y",H-4);t.setAttribute("text-anchor","end");t.setAttribute("style","font-size:10px;opacity:.7");t.textContent="Esri, HERE, Garmin, © OpenStreetMap contributors";svg.appendChild(t);
 let cur="",d="",last=null;const seg=[];
 for(let i=0;i<N;i++){if(!ok[i])continue;const c=sc(D.pts[i][3]||0);if(last&&last.c===c)last.p.push(i);else{if(last)last.p.push(i);last={c:c,p:[i]};seg.push(last)}}
 seg.forEach(g=>{const p=document.createElementNS(NS,"path");p.setAttribute("d","M"+g.p.map(i=>X(lo[i]).toFixed(1)+","+Y(la[i]).toFixed(1)).join("L"));p.setAttribute("fill","none");p.setAttribute("stroke",g.c);p.setAttribute("stroke-width","3.5");p.setAttribute("stroke-linecap","round");p.setAttribute("stroke-linejoin","round");svg.appendChild(p)});
 const dot=(i,col,r,tt)=>{const c=document.createElementNS(NS,"circle");c.setAttribute("cx",X(lo[i]));c.setAttribute("cy",Y(la[i]));c.setAttribute("r",r);c.setAttribute("fill",col);c.setAttribute("stroke","#fff");c.setAttribute("stroke-width","2");const t=document.createElementNS(NS,"title");t.textContent=tt;c.appendChild(t);svg.appendChild(c);return c};
 const fi=ok.indexOf(true),li=ok.lastIndexOf(true);dot(fi,"#18a058",6,"Start "+D.start);dot(li,"#d4332b",6,"End "+D.end);
 const near=(la_,lo_)=>{let b=0,bd=9e9;for(let i=0;i<N;i++){if(!ok[i])continue;const q=(la[i]-la_)**2+((lo[i]-lo_)*kx)**2;if(q<bd){bd=q;b=i}}return b};
 D.near=near;
 (D.flips||[]).forEach(e=>{if(e.la==null)return;const i=near(e.la,e.lo);dot(i,e.kind==="110->100"?"#d4332b":"#0a6cff",5,e.id+" "+e.kind+" "+e.time.substr(11,8)).style.cursor="pointer"});
 (D.saved||[]).forEach(e=>{let i=-1;if(e.t!=null){let bd=9e9;for(let k=0;k<N;k++){if(!ok[k])continue;const d=Math.abs(D.pts[k][0]-e.t);if(d<bd){bd=d;i=k}}if(bd>90)i=-1}if(i<0&&e.la!=null)i=near(e.la,e.lo);if(i>=0)dot(i,"#a855f7",8,"Saved clip: "+e.label+" "+e.time)});
 const hv=document.createElementNS(NS,"circle");hv.setAttribute("r",6);hv.setAttribute("fill","none");hv.setAttribute("stroke","#fff");hv.setAttribute("stroke-width","3");hv.style.display="none";svg.appendChild(hv);D.ok=ok;D.la=la;D.lo=lo;
 D.mapCursor=i=>{hv.setAttribute("cx",X(lo[i]));hv.setAttribute("cy",Y(la[i]));hv.style.display=""};D.mapCursorOff=()=>{hv.style.display="none"};
};
// Live map (MapLibre GL + OpenFreeMap vector tiles, no key): pan / zoom, and hover / click link to the charts and the footage.
// If the library or the tiles can't load (offline, no WebGL) it falls back to the static route picture above.
const liveMap=function(){
 const la=D.pts.map(p=>p[1]),lo=D.pts.map(p=>p[2]),ok=la.map((v,i)=>v!=null&&lo[i]!=null);if(!ok.some(x=>x))return false;
 const box=$("#mapbox"),sv=$("#map"),div=document.createElement("div");div.id="mapl";box.appendChild(div);sv.style.display="none";
 const L=la.filter((v,i)=>ok[i]),O=lo.filter((v,i)=>ok[i]);
 D.ok=ok;D.la=la;D.lo=lo;
 const kx=Math.cos((Math.min(...L)+Math.max(...L))/2*Math.PI/180);
 const near=(la_,lo_)=>{let b=0,bd=9e9;for(let i=0;i<N;i++){if(!ok[i])continue;const q=(la[i]-la_)**2+((lo[i]-lo_)*kx)**2;if(q<bd){bd=q;b=i}}return b};D.near=near;
 const dark=window.matchMedia&&matchMedia("(prefers-color-scheme: dark)").matches;
 const map=new maplibregl.Map({container:div,style:D.map.base+(dark?D.map.dark:D.map.light),bounds:[[Math.min(...O),Math.min(...L)],[Math.max(...O),Math.max(...L)]],fitBoundsOptions:{padding:40,maxZoom:16},attributionControl:{compact:true}});
 {const c=map.getContainer();map.scrollZoom.disable();c.addEventListener('mousedown',()=>map.scrollZoom.enable());c.addEventListener('mouseleave',()=>map.scrollZoom.disable())}
 const nav=new maplibregl.NavigationControl({showCompass:false});map.addControl(nav,"top-right");
 {const b=document.createElement("button");b.type="button";b.className="maplibregl-ctrl-icon";b.title="Show the whole route";b.setAttribute("aria-label","Show the whole route");b.innerHTML='<span style="display:block;text-align:center;font-size:18px;line-height:29px">\u2922</span>';
  b.onclick=()=>map.fitBounds([[Math.min(...O),Math.min(...L)],[Math.max(...O),Math.max(...L)]],{padding:40,maxZoom:16});nav._container.appendChild(b)}
 const mb=$("#mapbtns");if(mb)mb.innerHTML='<span class="sub" style="float:right;margin-top:2px;text-transform:none;font-weight:400;letter-spacing:0">Click the map, then scroll or pinch to zoom · hover the route for details · click it, or a purple saved-clip marker, to play footage'+(D.gpsnote?' · '+D.gpsnote:'')+'</span>';
 let loaded=false,dead=false;
 const fail=()=>{if(dead||loaded)return;dead=true;try{map.remove()}catch(e){}div.remove();sv.style.display="";staticMap()};
 setTimeout(fail,9000);map.on("error",()=>{if(!loaded)fail()});
 const segs=[];let last=null;
 for(let i=0;i<N;i++){if(!ok[i])continue;const c=sc(D.pts[i][3]||0);if(last&&last.c===c)last.p.push(i);else{if(last)last.p.push(i);last={c:c,p:[i]};segs.push(last)}}
 const lines={type:"FeatureCollection",features:segs.map(g=>({type:"Feature",properties:{c:g.c},geometry:{type:"LineString",coordinates:g.p.map(i=>[lo[i],la[i]])}}))};
 const pts=[],pt=(i,col,r,tt)=>pts.push({type:"Feature",properties:{col:col,r:r,tt:tt},geometry:{type:"Point",coordinates:[lo[i],la[i]]}});
 const fi=ok.indexOf(true),li=ok.lastIndexOf(true);
 (D.flips||[]).forEach(e=>{if(e.la==null)return;pt(near(e.la,e.lo),e.kind==="110->100"?"#d4332b":"#0a6cff",5,e.id+" "+e.kind+" "+e.time.substr(11,8))});
 // saved clips: placed where the car was at that moment (time), falling back to the event's own location
 const nearT=t=>{let b=-1,bd=9e9;for(let i=0;i<N;i++){if(!ok[i])continue;const d=Math.abs(T[i]-t);if(d<bd){bd=d;b=i}}return bd<=90?b:-1};
 (D.saved||[]).forEach(e=>{let i=e.t!=null?nearT(e.t):-1;if(i<0&&e.la!=null)i=near(e.la,e.lo);if(i<0)return;pts.push({type:"Feature",properties:{col:"#a855f7",r:9,tt:"Saved clip: "+e.label+" "+e.time+(e.t!=null?" - click to play":""),t:e.t==null?T[i]:e.t},geometry:{type:"Point",coordinates:[lo[i],la[i]]}})});
 pt(fi,"#18a058",7,"Start "+D.start);pt(li,"#d4332b",7,"End "+D.end);
 const none={type:"FeatureCollection",features:[]};
 const G=D.gps||[],gl={type:"FeatureCollection",features:[]};
 if(G.length>5){let cur=null;for(let k=0;k<G.length;k++){const c=sc(G[k][3]||0);if(cur&&cur.c===c)cur.p.push(k);else{if(cur)cur.p.push(k);cur={c:c,p:[k]};gl.features.push({type:"Feature",properties:{c:c},geometry:{type:"LineString",coordinates:cur.p.map(()=>null)},_g:cur})}}
  gl.features.forEach(f=>{f.geometry.coordinates=f._g.p.map(k=>[G[k][2],G[k][1]]);delete f._g})}
 map.on("load",()=>{if(dead)return;loaded=true;
  map.addSource("route",{type:"geojson",tolerance:0,data:lines});
  map.addLayer({id:"rc",type:"line",source:"route",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":"#fff","line-width":8,"line-opacity":.9}});
  map.addLayer({id:"rl",type:"line",source:"route",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":["get","c"],"line-width":4.5}});
  if(gl.features.length){map.setPaintProperty("rl","line-width",2);map.setPaintProperty("rl","line-opacity",.45);map.setPaintProperty("rc","line-opacity",.5);
   map.addSource("gps",{type:"geojson",tolerance:0,data:gl});
   map.addLayer({id:"gc",type:"line",source:"gps",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":"#fff","line-width":8,"line-opacity":.9}});
   map.addLayer({id:"gl",type:"line",source:"gps",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":["get","c"],"line-width":4.5}})}
  map.addSource("pts",{type:"geojson",data:{type:"FeatureCollection",features:pts}});
  map.addLayer({id:"pc",type:"circle",source:"pts",paint:{"circle-radius":["get","r"],"circle-color":["get","col"],"circle-stroke-color":"#fff","circle-stroke-width":2}});
  map.addSource("hv",{type:"geojson",data:none});
  map.addLayer({id:"hvl",type:"circle",source:"hv",paint:{"circle-radius":7,"circle-color":"#fff","circle-stroke-color":"#111","circle-stroke-width":2.5}});
  const pop=new maplibregl.Popup({closeButton:false,closeOnClick:false,offset:10});
  map.on("mousemove","pc",e=>{const f=e.features[0];map.getCanvas().style.cursor="pointer";rp.remove();pop.setLngLat(f.geometry.coordinates).setText(f.properties.tt).addTo(map)});
  map.on("mouseleave","pc",()=>{map.getCanvas().style.cursor="";pop.remove()});
  map.on("click","pc",e=>{const t=e.features[0].properties.t;if(t!=null&&D.seekFootage)D.seekFootage(+t)});
  // hover the route: time, speed, power, elevation, battery and distance so far
  const rp=new maplibregl.Popup({closeButton:false,closeOnClick:false,offset:14,className:"rp"}),o0=D.pts[ok.indexOf(true)][10];
  const info=i=>{const p=D.pts[i],km=p[10]!=null&&o0!=null?(p[10]-o0).toFixed(1)+" km into the drive":"";return `<b>${new Date(p[0]*1000).toISOString().substr(11,8)}</b> &nbsp;${f0(p[3])} km/h`+(p[4]!=null?` · ${p[4]} kW`:"")+`<br>Elevation ${f0(p[5])} m · Battery ${f0(p[6])}%`+(km?"<br>"+km:"")}
  const hit=pnt=>{let b=-1,bd=24*24;for(let i=0;i<N;i++){if(!ok[i])continue;const q=map.project([lo[i],la[i]]),d=(q.x-pnt.x)**2+(q.y-pnt.y)**2;if(d<bd){bd=d;b=i}}return b};
  let busy=false,on=false;
  map.on("mousemove",e=>{if(busy)return;busy=true;requestAnimationFrame(()=>{busy=false;const i=hit(e.point);if(i>=0){on=true;if(D.cursor)D.cursor(T[i]);if(!map.queryRenderedFeatures(e.point,{layers:["pc"]}).length)rp.setLngLat([lo[i],la[i]]).setHTML(info(i)).addTo(map)}else if(on){on=false;rp.remove();if(D.cursorOff)D.cursorOff()}})});
  map.on("mouseout",()=>{rp.remove();if(on){on=false;if(D.cursorOff)D.cursorOff()}});
  map.on("click",e=>{const i=hit(e.point);if(i>=0&&D.seekFootage)D.seekFootage(T[i])});
  D.mapCursor=i=>{const s=map.getSource("hv");if(s)s.setData({type:"Feature",properties:{},geometry:{type:"Point",coordinates:[lo[i],la[i]]}})};
  D.mapCursorOff=()=>{const s=map.getSource("hv");if(s)s.setData(none)};
 });
 D.map=map;return true};
(function(){let ok=false;try{ok=!!window.maplibregl&&liveMap()}catch(e){ok=false;try{const m=$("#mapl");if(m)m.remove();$("#map").style.display=""}catch(_){}}if(!ok)staticMap()})();
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def hhmm(s):
    return s[11:16] if s and len(s) >= 16 else ""


def build_page(stamp, m, win, flips, recs, saved_ev, cov, clips_rel, folder, mwin=None):
    t0 = win[0]["t"]
    epoch = lambda dt: (dt - datetime.datetime(1970, 1, 1)).total_seconds()
    pts = []
    for p in win:
        r1 = lambda x, n=1: None if x is None else round(x, n)
        pts.append([epoch(p["t"]), None if p["la"] is None else round(p["la"], 5), None if p["lo"] is None else round(p["lo"], 5),
                    r1(p["v"]), r1(p["p"]), r1(p["el"], 0), r1(p["b"], 0),
                    r1(p["rg"]), r1(p["it"]), r1(p["ot"]), r1(p["od"], 2)])
    start = (mwin[0] if mwin else win[0]["t"]).strftime("%H:%M")      # Tessie's own start/end minute when the master row is known
    end = (mwin[1] if mwin else win[-1]["t"]).strftime("%H:%M")
    fl = []
    for e in flips:
        fl.append(dict(id=e["id"], kind=e["flip"], time=e["time"], t=epoch(pdt(e["time"])), la=f(e["lat"]), lo=f(e["lon"]),
                       road=e.get("road_before", ""), vb=e.get("speed_before"), va=e.get("speed_after"), osm=e.get("osm_class", "")))
    sv = [dict(label=s["label"], time=s["time"], t=epoch(s["dt"]), la=s["la"], lo=s["lo"]) for s in saved_ev]
    data = dict(pts=pts, flips=fl, saved=sv, start=start, end=end, stamp=stamp,
                map=dict(base=CFG.get("map.tiles_base"), light=CFG.get("map.style_light"), dark=CFG.get("map.style_dark")))
    gps, ginfo = load_gps(folder, stamp, win[0]["t"], win[-1]["t"])
    if gps:
        data["gps"] = gps
        data["gpsnote"] = "Street-accurate dashcam GPS covers %s%% of this drive (thin grey = Tessie's own fixes)" % ginfo.get("coverage_pct", "?")
    if cov and cov.get('foot'):
        data['foot'] = cov['foot']

    frm = place_name(m, "Starting")
    to = place_name(m, "Ending")
    day = t0.strftime("%A %d %B %Y")
    dist = f(m.get("Distance (km)")) if m else None
    dur = f(m.get("Duration (Minutes)")) if m else None
    if dist is None:
        dist = (win[-1]["od"] or 0) - (win[0]["od"] or 0) if win[0]["od"] is not None else None
    if dur is None:
        dur = (win[-1]["t"] - win[0]["t"]).total_seconds() / 60
    vs = [p["v"] for p in win if p["v"] is not None]
    st = []
    st.append(("Distance", "%.1f km" % dist if dist is not None else "-"))
    if m and dist:
        ap = f(m.get("Autopilot Distance (km)"), 0)
        st.append(("Self-driving", "%d%%" % round(100 * ap / dist)))
    st.append(("Time", "%d h %02d min" % (int(dur // 60), int(dur % 60)) if dur >= 60 else "%d min" % round(dur)))
    st.append(("Average", "%s km/h" % (m.get("Average Speed (km/h)") if m else "-")))
    st.append(("Top speed", "%s km/h" % (m.get("Max Speed (km/h)") if m else (max(vs) if vs else "-"))))
    if m:
        st.append(("Energy", "%.1f kWh" % f(m.get("Total Energy Used (kWh)"), 0)))
        st.append(("Efficiency", "%s Wh/km" % m.get("Average Energy Used (Wh/km)")))
        st.append(("Battery", "%s → %s %%" % (m.get("Starting Battery (%)"), m.get("Ending Battery (%)"))))
        ap = f(m.get("Autopilot Distance (km)"), 0)
        if dist:
            st.append(("Autopilot/FSD", "%.1f km" % ap))
        od = [p["od"] for p in win if p["od"] is not None]
        if od:
            st.append(("Odometer", "%s → %s km" % (format(round(od[0]), ","), format(round(od[-1]), ","))))
        st.append(("Outside", "%s °C" % m.get("Average Outside Temperature (°C)")))
    def addr(k):
        a_ = (m.get(k) if m else "") or ""
        return a_.replace(", Australia", "")

    def bat(k, i):
        v = f(m.get(k)) if m else None
        if v is None:
            bs = [p["b"] for p in win if p["b"] is not None]
            v = bs[i] if bs else None
        return "" if v is None else "%d%%" % round(v)

    def block(col_, when, who, ad, b_):
        ad = ad if ad and ad != who else ""
        return (f'<div class="tp"><span class="dot" style="background:{col_}"></span><div><div class="w">{esc(when)}<i>battery {esc(b_)}</i></div>'
                f'<b>{esc(who)}</b><div class="ad">{esc(ad)}</div></div></div>')
    fmt_t = lambda d_: d_.strftime("%a %d %b %-I:%M %p").upper()
    trip = ('<div class="card"><div class="trip">' + block("var(--ok)", fmt_t(mwin[0] if mwin else win[0]["t"]), frm, addr("Starting Location"), bat("Starting Battery (%)", 0)) +
            block("var(--bad)", fmt_t(mwin[1] if mwin else win[-1]["t"]), to, addr("Ending Location"), bat("Ending Battery (%)", -1)) + '</div></div>')
    stats = "".join(f'<div class="stat"><b>{esc(v)}</b><span>{esc(k)}</span></div>' for k, v in st)

    svh = ""
    for s in reversed(saved_ev):
        v = f'<div style="margin-top:10px"><video controls preload="none" src="clips/{esc(s["mp4"])}"></video></div>' if s.get("mp4") else ""
        svh += f'<div class="card"><div><b>{esc(s["time"])}</b> · {esc(s["label"])} · {esc(s["place"])}</div>{v}</div>'
    covh = '<div class="card" id="foot"></div>' if cov else ""

    title = f"{t0.strftime('%a %d %b %H:%M')} {frm} → {to}"
    data['title'] = f"{frm} → {to}"
    up = "../../" if dp.DAY_RE.match(os.path.basename(os.path.dirname(folder))) else "../"        # the way back to deepdive/index.html from the drive's folder
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.css"><script src="https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.js"></script><style>{CSS}{DC.CSS}{FU.CSS}#mapl{{width:100%;height:520px;border-radius:8px;overflow:hidden}}.maplibregl-popup-content{{color:#111;font:12px system-ui,sans-serif;padding:6px 9px}}</style></head><body><div class="wrap">
<div class="sub"><a href="{up}index.html">All drives</a></div>
<h1>{esc(frm)} → {esc(to)}</h1><div class="sub">{esc(day)} · {esc(start)}–{esc(end)} · folder {esc(stamp)}</div>
{trip}<div class="card"><div class="stats">{stats}</div></div>
<div class="two"><div class="card"><h2>Route<span id="mapbtns"></span></h2><div id="mapbox"><svg id="map" viewBox="0 0 1060 540" style="width:100%;height:auto"></svg></div>
{MU.legend_html()}
<div class="legend"><span><i style="background:#a855f7;height:8px;width:8px;border-radius:50%"></i>saved clip</span></div></div>
<div class="card" id="charts"></div></div>
{covh}
{('<h2>Saved clips</h2>'+svh) if svh else ''}
<div class="sub">Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} by drive_report.py from this folder's Tessie CSV{' and TeslaCam footage' if cov else ''}.</div>
</div><div class="tip" id="tip"></div><script>const DATA={json.dumps(data, separators=(',', ':'))};{JS.replace('/*SC_JS*/', MU.sc_js())}{DC.JS}{FU.JS if cov else ''}</script></body></html>"""


# ----------------------------------------------------------------------------- per drive


def process_drive(csv_path, master, flips_by_file, recs, foot, args, base_analysis):
    stamp = dp.stamp_of(csv_path)
    folder = dp.drive_folder_of(csv_path)
    if not folder:
        return "skip (flat file - run migrate_drive_folders.py)"
    out = os.path.join(folder, "drive.html")
    pts = load_drive_csv(csv_path)
    if not pts:
        return "empty csv"
    mv = next((p["t"] for p in pts if (p["v"] or 0) > 0), pts[0]["t"])
    mm = find_master(master, mv)
    m = mm[2] if mm else None
    win = drive_window(pts, (mm[0], mm[1]) if mm else None)
    if len(win) < 2:
        return "too short"
    a, b = win[0]["t"], win[-1]["t"]
    # up to date = page newer than the CSV AND no new footage has appeared for this drive since it was built
    if os.path.exists(out) and not args.force and os.path.getmtime(out) >= max(os.path.getmtime(csv_path), CODE_MTIME, gps_mtime(folder, os.path.basename(folder))):
        sj, old = os.path.join(folder, "drive_summary.json"), {}
        try:
            old = json.load(open(sj))
            n_old = old["footage_clips"]
        except Exception:
            n_old = -1
        # the clock offset is worked out from the dashcam GPS once and kept in drive_summary.json (it is slow to recompute)
        off_now = old["foot_off"] if "foot_how" in old else (foot.best_offset(a, b, win)[0] if foot and foot.clips else 0)
        n_now = len(foot.in_window(a, b, off_now)) if foot and foot.clips else 0
        vids_ok = old.get("videos", False) or args.no_video or not n_now
        off_old = old.get("foot_off", 0)
        if n_now == n_old and vids_ok and off_now == off_old:
            if "foot_how" not in old:
                old["foot_off"], old["foot_how"] = off_now, foot.how if (foot and foot.clips) else ""
                with open(sj, "w", encoding="utf-8") as fh:
                    json.dump(old, fh, indent=1, ensure_ascii=False)
            return "up to date"
    flips = []          # a drive page is not analysed for FSD 100 <-> 110 speed changes (dashcam_flip_review.py still does, on its own)
    clips_dir = os.path.join(folder, "clips")
    clips_rel = set(os.listdir(clips_dir)) if os.path.isdir(clips_dir) else set()
    saved_ev, cov = [], None
    incomplete = False          # a snippet was skipped (budget reached) -> rebuild this drive next run

    off = 0
    if foot and foot.clips:
        off, n = foot.best_offset(a, b, win)
        try:
            off_old = json.load(open(os.path.join(folder, "drive_summary.json"))).get("foot_off", 0)
        except Exception:
            off_old = 0
        if off != off_old and os.path.isdir(clips_dir):      # snippets cut with another clock offset show the wrong moment: cut them again
            for nm in os.listdir(clips_dir):
                if nm.startswith("saved_") and nm.endswith(".mp4"):
                    try:
                        os.remove(os.path.join(clips_dir, nm))
                    except OSError:
                        pass
            clips_rel = {x for x in clips_rel if not x.startswith("saved_")}
        # saved events in the window (clip clock) that sit near the route
        for path, ev in foot.saved:
            try:
                # the folder name is on the clip clock (event.json is on the car's current local time, which can differ by an hour)
                ts = datetime.datetime.strptime(os.path.basename(path)[:19], "%Y-%m-%d_%H-%M-%S") + datetime.timedelta(seconds=off)
            except Exception:
                continue
            if not (a - datetime.timedelta(minutes=1) <= ts <= b + datetime.timedelta(minutes=1)):
                continue
            la, lo = f(ev.get("est_lat")), f(ev.get("est_lon"))
            if la is not None and any(p["la"] is not None for p in win):
                dmin = min(D.hav_km(la, lo, p["la"], p["lo"]) for p in win if p["la"] is not None)
                if dmin > 5:
                    continue
            reason = ev.get("reason", "")
            label = REASONS.get(reason, reason.replace("_", " "))
            rec = dict(label=label, time=ts.strftime("%H:%M:%S"), dt=ts, place=", ".join(x for x in (ev.get("city"), ev.get("street")) if x), la=la, lo=lo,
                       folder=os.path.basename(path), mp4=None)
            name = "saved_%s.mp4" % os.path.basename(path)
            if name in clips_rel:
                rec["mp4"] = name
            elif not args.no_video:
                if time.time() < args.deadline:
                    os.makedirs(clips_dir, exist_ok=True)
                    if foot.cut(ts, 15, 15, off, os.path.join(clips_dir, name), saved_pool(path)):
                        rec["mp4"] = name
                        clips_rel.add(name)
                else:
                    incomplete = True
            saved_ev.append(rec)
        # coverage
        inw = foot.in_window(a, b, off)
        if inw:
            span = (b - a).total_seconds() or 1
            segs = []
            for s, p in inw:
                x0 = max(0.0, (s + datetime.timedelta(seconds=off) - a).total_seconds() / span)
                x1 = min(1.0, (s + datetime.timedelta(seconds=off + 60) - a).total_seconds() / span)
                if x1 > x0:
                    segs.append((x0, x1))
            tot = sum(y - x for x, y in segs)
            kinds = {"recent": 0, "saved": 0, "sentry": 0}
            for s, p in inw:
                k = "saved" if "SavedClips" in p else "sentry" if "SentryClips" in p else "recent"
                kinds[k] += 1
            cov = dict(segs=segs, n=len(inw), pct=min(100.0, tot * 100), **kinds)
            rows = []
            for s_, p_ in inw:
                ref = clip_ref(p_, foot.roots)
                if ref:
                    k_ = "s" if "SavedClips" in ref[1] else "y" if "SentryClips" in ref[1] else "r"
                    rows.append([int((s_ - datetime.datetime(1970, 1, 1)).total_seconds() + off), ref[0], ref[1], k_, ref[2], ref[3]])
            cov["foot"] = dict(c=rows, off=off)
        # stills + snippets for the flips that have footage
        for e in flips:
            r = recs.get(e["id"])
            if not r:
                continue
            sheet = os.path.join(base_analysis, "dashcam_flip_review", r.get("sheet", ""))
            nm = e["id"] + ".jpg"
            big = nm in clips_rel and os.path.getsize(os.path.join(clips_dir, nm)) > 1_200_000     # an earlier full-size copy: shrink it
            if os.path.exists(sheet) and (nm not in clips_rel or big):
                os.makedirs(clips_dir, exist_ok=True)
                try:                       # half-size copy (the mp4 snippet carries the detail): ~0.4 MB instead of ~2 MB
                    from PIL import Image
                    im = Image.open(sheet)
                    im = im.resize((im.size[0] // 2, im.size[1] // 2), Image.LANCZOS)
                    im.save(os.path.join(clips_dir, nm), "JPEG", quality=70, optimize=True)
                except Exception:
                    shutil.copy2(sheet, os.path.join(clips_dir, nm))
                clips_rel.add(nm)
            mp = e["id"] + ".mp4"
            if mp not in clips_rel and not args.no_video and time.time() >= args.deadline:
                incomplete = True
            if mp not in clips_rel and not args.no_video and time.time() < args.deadline:
                try:
                    T = datetime.datetime.strptime(r["onset_local"], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                os.makedirs(clips_dir, exist_ok=True)
                if foot.cut(T, 12, 16, r.get("clip_offset_h", 0) * 3600, os.path.join(clips_dir, mp)):
                    clips_rel.add(mp)

    html_ = build_page(stamp, m, win, flips, recs, saved_ev, cov, clips_rel, folder, (mm[0], mm[1]) if mm else None)
    summ = dict(stamp=stamp, start=win[0]["t"].strftime("%Y-%m-%d %H:%M:%S"), end=win[-1]["t"].strftime("%Y-%m-%d %H:%M:%S"),
                frm=place_name(m, "Starting"), to=place_name(m, "Ending"),
                km=(m or {}).get("Distance (km)", ""), minutes=(m or {}).get("Duration (Minutes)", ""),
                avg_kmh=(m or {}).get("Average Speed (km/h)", ""), max_kmh=(m or {}).get("Max Speed (km/h)", ""),
                wh_km=(m or {}).get("Average Energy Used (Wh/km)", ""), kwh=(m or {}).get("Total Energy Used (kWh)", ""),
                batt_start=(m or {}).get("Starting Battery (%)", ""), batt_end=(m or {}).get("Ending Battery (%)", ""),
                autopilot_km=(m or {}).get("Autopilot Distance (km)", ""),
                flips=len(flips), flips_with_footage=sum(1 for e in flips if e["id"] in recs), saved_clip_events=len(saved_ev),
                footage_clips=cov["n"] if cov else 0, footage_pct=round(cov["pct"]) if cov else 0,
                videos=(not args.no_video) and not incomplete, foot_off=off, foot_how=foot.how if (foot and foot.clips) else "")
    # times in the data are UTC; the zone and offset are kept so the local clock can be shown again (the day folder is the local date of the start)
    _u0, _u1 = CC.from_local(CC.naive(summ["start"].replace(" ", "_"))), CC.from_local(CC.naive(summ["end"].replace(" ", "_")))
    summ.update(t0=_u0, t1=_u1, tz=getattr(CC.zone(), "key", "UTC"), utc_offset_s=CC.local_offset(_u0))
    with open(os.path.join(folder, "drive_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summ, fh, indent=1, ensure_ascii=False)
    with open(out + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(html_)
    os.replace(out + ".tmp", out)
    return "ok" + (" +footage" if cov else "")


def default_data():
    return D.DEFAULT_DATA


def build_index(root, master, rows):
    """rows: [(sortkey, info dict)] -> deepdive/index.html (sortkey = true local start)"""
    rows.sort(key=lambda r: (r[1].get("utc") or 0, r[0]), reverse=True)      # newest first by the UTC start (never repeats or skips); the times shown are local
    body, last = "", None
    for stamp, i in rows:
        day = stamp[:10]
        if day != last:
            body += f'<tr class="day"><td colspan="9"><b>{datetime.datetime.strptime(day, "%Y-%m-%d").strftime("%A %d %B %Y")}</b></td></tr>'
            last = day
        body += (f'<tr data-q="{esc((stamp+" "+i["stamp"]+" "+i["frm"]+" "+i["to"]).lower())}"><td><a href="{esc(i.get("href") or i["stamp"] + "/drive.html")}">{esc(stamp[11:16].replace("-", ":"))}</a></td>'
                 f'<td>{esc(i["frm"])} → {esc(i["to"])}</td><td>{esc(i["km"])}</td><td>{esc(i["min"])}</td><td>{esc(i["avg"])}</td><td>{esc(i["max"])}</td>'
                 f'<td>{esc(i["wh"])}</td><td>{esc(i["flips"])}</td><td>{esc(i["foot"])}</td></tr>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>All drives</title><style>{CSS}
.day td{{background:var(--bg);padding-top:14px}}input{{width:100%;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);margin-bottom:10px}}</style></head>
<body><div class="wrap"><h1>All drives</h1><div class="sub">{len(rows)} drives with Tessie telemetry. Each opens its own summary page (map, graph, FSD speed changes, dashcam footage).</div>
<input id="q" placeholder="Filter by date, place..."><div class="card" style="overflow-x:auto"><table><thead><tr><th>Time</th><th>Trip</th><th>km</th><th>min</th><th>avg</th><th>max</th><th>Wh/km</th><th>100↔110</th><th>Dashcam</th></tr></thead><tbody id="tb">{body}</tbody></table></div></div>
<script>const q=document.getElementById('q');q.addEventListener('input',()=>{{const s=q.value.toLowerCase();document.querySelectorAll('#tb tr[data-q]').forEach(r=>r.style.display=r.dataset.q.includes(s)?'':'none');document.querySelectorAll('#tb tr.day').forEach(d=>{{let n=d.nextElementSibling,any=false;while(n&&!n.classList.contains('day')){{if(n.style.display!=='none')any=true;n=n.nextElementSibling}}d.style.display=any?'':'none'}})}});</script></body></html>"""
    with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=default_data())
    ap.add_argument("--teslacam", action="append", default=None)
    ap.add_argument("--since", default="")
    ap.add_argument("--footage-since", default="2026-08-01", help="oldest TeslaCam folder date to scan (default 2026-08-01)")
    ap.add_argument("--with-secondary", action="store_true", help="also read the JOWUA and Tesla USB drives (default: the Archive only, found by its marker folder)")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--budget", type=float, default=0)
    a = ap.parse_args()
    t_start = time.time()
    a.deadline = t_start + (a.budget if a.budget else 1e9)
    data = os.path.abspath(os.path.expanduser(a.data_dir))
    root = os.path.join(data, "drives", "deepdive")
    master, flips, recs = load_master(data), load_flips(data), load_recs(data)
    PLACES[:] = load_places(data)
    roots = [os.path.expanduser(c) for c in (a.teslacam or DR.teslacam_roots(a.with_secondary)) if os.path.isdir(os.path.expanduser(c))]
    foot = Footage(roots, a.footage_since) if roots else None
    if foot:            # offsets already settled by GPS on earlier runs help the drives GPS cannot settle
        for _st, _fo in dp.iter_folders(root):
            sj = os.path.join(_fo, "drive_summary.json")
            try:
                j = json.load(open(sj))
                if j.get("foot_how") == "gps":
                    foot.verified.append((datetime.datetime.strptime(j["start"], "%Y-%m-%d %H:%M:%S"), j["foot_off"]))
            except Exception:
                pass
    print(f"{len(master)} drives in master, {sum(len(v) for v in flips.values())} flips, {len(recs)} flips with footage, "
          f"{len(foot.clips) if foot else 0} clips ({len(roots)} TeslaCam folder(s))", flush=True)
    base_analysis = os.path.join(data, "drives", "analysis")
    only = set(x for x in a.only.split(",") if x)
    done, skipped, rows = 0, 0, []
    for p in dp.list_csvs(root):
        stamp = dp.stamp_of(p)
        if only and stamp not in only:
            continue
        if a.since and stamp[:10] < a.since:
            continue
        if time.time() > a.deadline:
            print("budget reached - run again to continue", flush=True)
            break
        res = process_drive(p, master, flips, recs, foot, a, base_analysis)
        if res.startswith("ok"):
            done += 1
            print(f"  {stamp}: {res}", flush=True)
        else:
            skipped += 1
    # index (cheap; from each folder's drive_summary.json)
    for p in dp.list_csvs(root):
        folder = dp.drive_folder_of(p)
        sj = os.path.join(folder, "drive_summary.json") if folder else ""
        if not sj or not os.path.exists(sj) or not os.path.exists(os.path.join(folder, "drive.html")):
            continue
        try:
            j = json.load(open(sj))
        except Exception:
            continue
        try:
            mm = find_master(master, datetime.datetime.strptime(j["start"], "%Y-%m-%d %H:%M:%S"))
            utc = int(mm[2]["Started At (UTC)"]) if mm else 0
        except Exception:
            utc = 0
        rows.append((j["start"][:10] + "_" + j["start"][11:16].replace(":", "-"), dict(utc=utc, stamp=j["stamp"], href=os.path.relpath(folder, root).replace(os.sep, "/") + "/drive.html", frm=j["frm"], to=j["to"], km=j["km"], min=j["minutes"], avg=j["avg_kmh"],
                                 max=j["max_kmh"], wh=j["wh_km"], flips=j["flips"] or "", foot=("%d%%" % j["footage_pct"]) if j["footage_clips"] else "")))
    build_index(root, master, rows)
    print(f"built {done} page(s), {skipped} skipped, index of {len(rows)} drives -> {root}/index.html", flush=True)


if __name__ == "__main__":
    main()
