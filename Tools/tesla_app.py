#!/usr/bin/env python3
"""tesla_app.py - the Tesla app: one window for activity, drives, charges, idles, battery, analytics and dashcam footage (REQ-199).

NAME
    tesla_app.py - local web app (Python only, no extra packages needed) shown in its own window

SYNOPSIS
    tesla_app.py [--data-dir DIR] [--port N] [--no-window] [--browser]

DESCRIPTION
    Starts a small server on 127.0.0.1 that reads the Tesla data folder (the CSV / JSON files the sync scripts already
    write; nothing is changed by looking) and shows it in the style of Tessie: an Activity timeline of drives, charges and
    idles; a page per drive (map, stacked charts, dashcam footage), per charge (power curve) and per idle; battery health;
    monthly analytics; a Places manager (saved places, Tesla Supercharger names, nearby OpenStreetMap places); and the dashcam tools. A TeslaCam page plays the Sentry, Saved and Recent clips of any connected TeslaCam drive (all six cameras, or one; a Sentry event opens at the moment it happened on the camera that saw it). Each drive has Overview, Footage and Review tabs (Review: the car's speed and self-driving state, speed-limit signs read from the video, lasting speed changes; reading signs needs OpenCV, ffmpeg and tesseract, see requirements.txt). The Drives list can be filtered by length and average speed. Dashcam clips are streamed from the Archive drive (found by what is on it,
    see lib/drive_roles.py, never by its volume name). Footage actions queued on a drive page (copy / delete / save as a
    clip) run from the Queue page: a preview first, then for real.

    The window is a native one when the optional package pywebview is installed (pip install pywebview); otherwise the app
    only serves (it never opens a browser by itself; open the printed URL yourself).

OPTIONS
    --data-dir DIR   Tesla data folder (default: TESLA_DATA_DIR or the iCloud "Tesla" folder)
    --port N         port to listen on (default 8765, or app.window.port in config.json; 0 = any free port)
    --no-window      only run the server (open http://127.0.0.1:PORT yourself)
    --browser        no native window, just serve (same as --no-window but the app keeps running quietly)

EXAMPLES
    Put each command on its own line. Do not paste a trailing "# comment" after a command: an interactive zsh does not
    treat "#" as a comment, so the comment arrives as extra arguments (this script now ignores them with a warning).

        cd path/to/tesla/Tools
        ./tesla_app.py
        ./tesla_app.py --browser
        ./tesla_app.py --no-window --port 8800
        pip install -r ../requirements.txt
        ./build_mac_app.sh

    ./tesla_app.py opens a native window if pywebview is installed, otherwise nothing opens (it never launches a browser). pywebview is in requirements.txt, so direnv
    installs it into the repo venv on its own (never install it globally). ./build_mac_app.sh makes ~/Applications/Tesellate.app to
    double-click (log: ~/Library/Logs/tesla_app.log). Stop the app by closing its window or Ctrl-C in the terminal.

FILES
    lib/app_data.py  the data layer (read-only)   lib/app_ui/  the page (index.html, app.css, app.js)
    lib/app_places.py  places: saved places, Tesla names, nearby OpenStreetMap places. The only thing the app writes is
                     places.json, when you press Save / Delete on a place (a dated backup is kept first)
    drive_actions.py runs the footage queue       build_mac_app.sh  makes a double-clickable Tesellate.app
"""
import argparse
import io
import json
import mimetypes
import html
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CMDS = os.path.join(HERE, "commands")          # the command-line scripts the app runs (the syncs); nothing is imported from there
sys.path.insert(0, os.path.join(HERE, "lib"))
import app_config  # noqa: E402
import config_backup  # noqa: E402
import app_data  # noqa: E402
import app_sentry  # noqa: E402
import app_teslacam  # noqa: E402
import app_drivelist  # noqa: E402
import home_ignore  # noqa: E402
import clips_purged  # noqa: E402
import teslacam_timeline as TLN  # noqa: E402
import run_cuts  # noqa: E402
import minute_kind  # noqa: E402
import mac_shell  # noqa: E402
import app_drive_review  # noqa: E402
import app_footage
import drive_eject
import footage_todo
import clip_keep
import clip_files
import stills_thin
import app_keeper  # noqa: E402
import app_charges  # noqa: E402
import clip_clock as CC  # noqa: E402
import geo_common as GEO  # noqa: E402
import school_days as SD  # noqa: E402
import app_sync  # noqa: E402
import app_software  # noqa: E402
import car_watch  # noqa: E402
import app_route  # noqa: E402
import app_kept  # noqa: E402
import app_reach  # noqa: E402
from fetch_ledger import FetchLedger  # noqa: E402
import drive_roles as DR  # noqa: E402
import drive_paths as DP  # noqa: E402

UI = os.path.join(HERE, "lib", "app_ui")
DEFAULT_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
mimetypes.add_type("text/javascript", ".js")
STORE = None
DRIVELIST = None
SENTRY = None
TC = None
KINDS = None
DRV = None                  # the manual drive review (lib/app_drive_review.py)
KEEP = None                 # reads the connected TeslaCam drives in the background (lib/app_keeper.py)
TODO = None                 # what each connected drive still needs (lib/footage_todo.py)
STHIN = None                # making the stills of ordinary driving smaller (lib/stills_thin.py)
CKEEP = None                # per clip: what is kept from its video, and deleting / moving the video (lib/clip_keep.py)
FG = None                   # the footage log and the one-second stills (lib/app_footage.py)
SYNC = None
REACH = None                # the Places map's range shading and coverage gaps (lib/app_reach.py)
TSYNC = None
CSYNC = None     # the Charges page's Tessie sync (sync_charging_all.sh)
FSYNC = None     # fetches a newly published month of FuelCheck prices (fuel_auto)
CCOST = None     # sending the charges' costs to Tessie (analyse_charging.py --sync-tessie-costs --apply)
SOFT = None      # the Software page's fetch of Teslascope's versions (lib/app_software.py)
CARW = None      # looks at which software the car is on, every software.car_check_minutes (lib/car_watch.py)
BOOK = None
STARTED = time.time()
DATA_DIR = DEFAULT_DATA
FOOT_EXT = (".mp4", ".jpg", ".jpeg", ".png", ".json")
_ACTION_LOCK = threading.Lock()
_ROOTS = {"t": 0, "v": None}


def drive_review_state(stamp):
    """What the Review tab needs: the finished review of this drive (None until one was run), the job (running for this drive or not), the defaults,
    and whatever signs reading needs that is missing."""
    import app_review
    job = DRV.status()
    return dict(result=DRV.result(stamp), job=job if job.get("stamp") == stamp else dict(running=False), defaults=DRV.defaults(),
                connected=bool(footage_roots()), missing=app_review.requirements())


_VOLS = [None]


def _volumes_changed():
    """True once each time the mounted volumes change (/Volumes changes when a drive is plugged in or ejected)."""
    try:
        m = os.stat("/Volumes").st_mtime
    except OSError:
        return False
    if _VOLS[0] is None:
        _VOLS[0] = m
        return False
    if m != _VOLS[0]:
        _VOLS[0] = m
        return True
    return False


def footage_root(role):
    """TeslaCam folder of the drive with this role (A Archive, J JOWUA, U USB); looked up at most every 20 s."""
    if _ROOTS["v"] is None:                                 # the first look waits for the answer
        _ROOTS["v"] = {r: DR.find_root(r) for r in DR.read_roles()}
        _ROOTS["t"] = time.time()
    elif (time.time() - _ROOTS["t"] > 20 or _volumes_changed()) and not _ROOTS.get("busy"):      # a drive plugged in or out changes /Volumes: looked at again at once
        _ROOTS["busy"] = True                               # later looks answer from the last one and refresh it in the background (a slow drive must not hold a request)

        def look():
            try:
                _ROOTS["v"] = {r: DR.find_root(r) for r in DR.read_roles()}
            finally:
                _ROOTS["t"] = time.time()
                _ROOTS["busy"] = False
        threading.Thread(target=look, daemon=True).start()
    return _ROOTS["v"].get(role)


def eject_drive(role, root):
    """Everything the app is reading from the drives is stopped first (the Keeper, the log, the stills, the telemetry scan), then the drive is ejected."""
    was = KEEP.paused
    KEEP.pause()
    FG.stop()
    TC.scan_stop()
    end = time.time() + 10
    while time.time() < end and (FG.status().get("running") or TC.scan_status().get("running")):
        time.sleep(0.3)
    res = drive_eject.eject(root)
    _ROOTS["v"] = {r: DR.find_root(r) for r in DR.read_roles()}        # look again now, not in 20 s
    _ROOTS["t"] = time.time()
    KEEP.paused = was                                         # as it was: an eject must not start a pass over the other drives
    return res


def thin_scope(items):
    """The clips named by the page ([{kind, id}]) -> ({(kind, id)}, {their minutes}), or (None, None) for no scope: shrinking then covers everything."""
    items = [(str(x.get("kind")), str(x.get("id"))) for x in (items or []) if isinstance(x, dict) and x.get("kind") in ("recent", "saved", "sentry") and x.get("id")]
    if not items:
        return None, None
    CKEEP.status()
    stamps = set()
    for k in items:
        stamps.update(CKEEP._groups.get(k, ()))
    return set(items), stamps


def footage_roots():
    """{role: TeslaCam folder} of every connected drive."""
    return {r: footage_root(r) for r in DR.read_roles() if footage_root(r)}


def all_sentry():
    """The saved Sentry events of every connected drive, each once: an event on several drives is listed on the one named first (Archive, then JOWUA,
    then Tesla USB: the biggest drive first). Each carries `role` (where to play it from) and `roles` (every connected drive holding it)."""
    seen, ok = {}, False
    for r in DR.read_roles():
        root = footage_root(r)
        if not root:
            continue
        ev, good = SENTRY.events(root) if r == "A" else TC.events(r, "sentry", root)
        ok = ok or good
        for e in ev:
            if e["id"] in seen:
                seen[e["id"]]["roles"].append(r)
            else:
                seen[e["id"]] = dict(e, role=r, roles=[r])
    return sorted(seen.values(), key=lambda e: e["t"], reverse=True), ok


def code_updated():
    """True when a program file changed after this server started (so a restart is needed to use it): the app's own files, and every module the
    server has actually imported from this folder or lib/ (a library edited under it counts, not only the app_* files)."""
    newest = 0
    for d, pat in ((HERE, ".py"), (os.path.join(HERE, "lib"), ".py")):
        try:
            for f in os.listdir(d):
                if f.endswith(pat) and (f.startswith("app_") or f in ("tesla_app.py", "footage_actions.py", "places_common.py")):
                    newest = max(newest, os.path.getmtime(os.path.join(d, f)))
        except OSError:
            pass
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None) or ""
        if f.endswith(".py") and os.path.abspath(f).startswith(HERE + os.sep):
            try:
                newest = max(newest, os.path.getmtime(f))
            except OSError:
                pass
    return newest > STARTED + 2


def ui_stamp():
    """Modified time of the page files (index.html, app.js, app.css): the page remembers the value it loaded with and says so when it changes."""
    t = 0
    for f in ("index.html", "app.js", "app.css"):
        try:
            t = max(t, os.path.getmtime(os.path.join(UI, f)))
        except OSError:
            pass
    return round(t, 1)


FLIP_LEGEND = ('<span><i style="background:#d4332b;height:8px;width:8px;border-radius:50%"></i>110→100</span>'
               '<span><i style="background:#0a6cff;height:8px;width:8px;border-radius:50%"></i>100→110</span>')


def drop_flips(h):
    """A drive page built when drive_report.py still looked for FSD 100 <-> 110 speed changes: take those markers and their legend out
    (a drive is not analysed for them any more; pages built now have none)."""
    i = h.find("const DATA=")
    if i < 0:
        return h
    try:
        obj, end = json.JSONDecoder().raw_decode(h, i + len("const DATA="))
    except ValueError:
        return h
    if obj.get("flips"):
        obj["flips"] = []
        h = h[:i] + "const DATA=" + json.dumps(obj, separators=(",", ":")) + h[end:]
    return h.replace(FLIP_LEGEND, "")


def fuse_into(h, p, on=True):
    """Supplement with TeslaCam footage. A drive page is made from Tessie (or another provider): the full picture, at low resolution. The dashcam can add
    detail at a higher resolution, so the tick box under the map exists only when BOTH are there: the page is a provider's, and the dashcam has telemetry for
    the drive (otherwise there is nothing to choose, no tick box). A drive with only the dashcam has no provider page and no tick box. Ticked, the page's points (Tessie's, about one every 30 s) become the dashcam's per-second readings, with
    accelerator / steering / indicator / FSD columns and the panels that draw them (drive_enrich_ui.py)."""
    if TC is None:
        return h
    import drive_enrich_ui as EU
    i = h.find("const DATA=")
    if i < 0:
        return h
    try:
        obj, end = json.JSONDecoder().raw_decode(h, i + len("const DATA="))
        new = TC.enrich(obj["pts"])
    except Exception:
        return h
    if not new:
        return h
    js = EU.tick_js(on)
    if on:
        obj["pts"] = new["rows"]
        h = h[:i] + "const DATA=" + json.dumps(obj, separators=(",", ":")) + h[end:]
        js = EU.js() + js
    return h.replace("</body>", "<script>%s</script></body>" % js, 1)


def drive_page(p, enrich=True):
    """A per-drive page as the generator wrote it; pages built before the map got its reset-view button get it here (no rebuild needed)."""
    with open(p, encoding="utf-8") as fh:
        h = fh.read()
    old = 'map.addControl(new maplibregl.NavigationControl({showCompass:false}),"top-right");'
    import drive_map_ui
    h = drive_map_ui.patch(h)
    h = drop_flips(h)
    h = fuse_into(h, p, enrich)
    if "Show the whole route" in h or old not in h:
        return h
    return h.replace(old, 'const nav=new maplibregl.NavigationControl({showCompass:false});map.addControl(nav,"top-right");\n {const b=document.createElement("button");b.type="button";b.className="maplibregl-ctrl-icon";b.title="Show the whole route";b.setAttribute("aria-label","Show the whole route");b.innerHTML=\'<span style="display:block;text-align:center;font-size:18px;line-height:29px">\\u2922</span>\';\n  b.onclick=()=>map.fitBounds([[Math.min(...O),Math.min(...L)],[Math.max(...O),Math.max(...L)]],{padding:40,maxZoom:16});nav._container.appendChild(b)}')


def _lo_arg(v):
    """The offset (seconds ahead of UTC) of the clock a page wants times shown on, or None for app.timezone's."""
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _utc_arg(txt):
    """UTC epoch seconds of a time the page sent: a number (UTC, what the page sends), else a local ISO text read on app.timezone's clock (never this
    computer's). ValueError when it is neither."""
    return CC.parse_local(txt)


COPIER = clip_files.Copier()


def safe_join(root, rel):
    p = os.path.realpath(os.path.join(root, rel))
    r = os.path.realpath(root)
    return p if (p == r or p.startswith(r + os.sep)) else None


class H(BaseHTTPRequestHandler):
    server_version = "TeslaApp/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- helpers
    def send_bytes(self, body, ctype, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj, separators=(",", ":")).encode(), "application/json; charset=utf-8", code)

    def send_file(self, path, cache=False):
        try:
            size = os.path.getsize(path)
            f = open(path, "rb")
        except OSError:
            return self.send_bytes(b"not found", "text/plain", 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "text/javascript"):
            ctype += "; charset=utf-8"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    f.close()
                    return self.send_bytes(b"", "text/plain", 416, {"Content-Range": "bytes */%d" % size})
                code = 206
        n = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(n))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        try:
            if self.command != "HEAD":
                f.seek(start)
                left = n
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            f.close()

    # ---- routes
    def do_HEAD(self):
        self.do_GET()

    def solaredge_oauth(self, path, q):
        from lib import solaredge_v2 as SE2
        page = lambda title, body: self.send_bytes(("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>%s</title><style>body{font:16px system-ui;max-width:560px;margin:12vh auto;padding:0 16px;color:#14171f}a{color:#3b6cf6}</style><h1>%s</h1>%s" % (title, title, body)).encode("utf-8"), "text/html; charset=utf-8")
        if path == "/solaredge/connect":
            try:
                url = SE2.authorize_url()
            except SE2.V2Error as e:
                return page("SolarEdge", "<p>%s</p>" % html.escape(e.detail))
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            return None
        code, site = (q.get("code") or [""])[0], (q.get("site_id") or [""])[0]
        if not code:
            return page("SolarEdge", "<p>Not approved: %s</p><p><a href='/solaredge/connect'>Connect again</a></p>" % html.escape((q.get("error_description") or q.get("error") or ["no code came back"])[0]))
        try:
            SE2.exchange_code(code, site or None)
        except SE2.V2Error as e:
            return page("SolarEdge", "<p>%s</p><p><a href='/solaredge/connect'>Connect again</a></p>" % html.escape(e.detail))
        mine = str(SE2.status().get("site_id") or "")
        warn = "<p>Approved site %s, configured site is %s.</p>" % (html.escape(site), html.escape(mine)) if site and mine and site != mine else ""
        return page("SolarEdge connected", "<p>Site %s.</p>%s<p><a href='/#/charges'>Back to the app</a></p>" % (html.escape(site or mine), warn))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        try:
            if path in ("/", "/index.html"):
                return self.send_file(os.path.join(UI, "index.html"))
            if path in ("/app.js", "/app.css"):
                return self.send_file(os.path.join(UI, path[1:]))
            if path == "/icon.png":                 # the app icon, also the tab/window icon
                return self.send_file(os.path.join(UI, "icon", "icon.png"), cache=True)
            if path in ("/solaredge/connect", "/solaredge/callback"):      # SolarEdge V2: the site owner approves the app here (lib/solaredge_v2.py)
                return self.solaredge_oauth(path, urllib.parse.parse_qs(u.query))
            if path.startswith("/api/"):
                t_in = time.time()
                try:
                    return self.api(path[5:], urllib.parse.parse_qs(u.query))
                finally:
                    if time.time() - t_in > 0.5:                # what is slow shows in the log (~/Library/Logs/tesla_app.log)
                        print("(slow: %s took %.1f s)" % (path, time.time() - t_in), flush=True)
            if path.startswith("/data/"):
                p = safe_join(DATA_DIR, path[6:])
                rel = path[6:].split("/")
                if len(rel) >= 4 and rel[0] == "drives" and rel[1] == "deepdive" and DP.STAMP_RE.match(rel[2]):    # a drive's file by its stamp, in whichever layout the folder is
                    f = DP.find_folder(os.path.join(DATA_DIR, "drives", "deepdive"), rel[2])
                    if f:
                        p = safe_join(f, "/".join(rel[3:])) or p
                if p and os.path.isfile(p) and p.endswith("drive.html") and os.sep + "deepdive" + os.sep in p:
                    return self.send_bytes(drive_page(p, urllib.parse.parse_qs(u.query).get("enrich", ["1"])[0] != "0").encode("utf-8"), "text/html; charset=utf-8")
                if p and os.path.isfile(p):
                    return self.send_file(p)
                return self.send_bytes(b"not found", "text/plain", 404)
            if path.startswith("/review/"):         # a small picture of a sign that was read
                p = safe_join(os.path.join(DATA_DIR, "drive_review", "crops"), path[8:])
                if p and os.path.isfile(p) and p.endswith(".jpg"):
                    return self.send_file(p, cache=True)
                return self.send_bytes(b"not found", "text/plain", 404)
            if path.startswith("/still/"):          # one kept still: /still/<clip stamp>/<second>.jpg (the video need not exist any more)
                m = re.match(r"^/still/(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})/(\d{1,2})\.jpg$", path)
                b = FG.still(m.group(1), int(m.group(2))) if m else None
                if b:
                    return self.send_bytes(b, "image/jpeg", extra={"Cache-Control": "public, max-age=31536000"})
                return self.send_bytes(b"not found", "text/plain", 404)
            if path.startswith("/evstill/"):        # one picture around an event: /evstill/<saved|sentry>/<event folder>/<clip stamp>/[<camera>/]<second>[h].jpg (h: half a second later; the video need not exist any more)
                m = re.match(r"^/evstill/(saved|sentry)/(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})/(?:([a-z_]+)/)?(\d{1,2})(h?)\.jpg$", path)
                b = CKEEP.evst.get(m.group(1), m.group(2), m.group(3), int(m.group(5)), cam=m.group(4), half=bool(m.group(6))) if m else None
                if b:
                    return self.send_bytes(b, "image/jpeg", extra={"Cache-Control": "public, max-age=31536000"})
                return self.send_bytes(b"not found", "text/plain", 404)
            if path.startswith("/eventshot/"):      # a full-size picture of the moment of an event: /eventshot/<saved|sentry>/<event folder>/<camera>.jpg
                m = re.match(r"^/eventshot/(saved|sentry)/(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})/([a-z_]+)\.jpg$", path)
                p = CKEEP.shots.path(m.group(1), m.group(2), m.group(3)) if m else ""
                if p:
                    return self.send_file(p, cache=True)
                return self.send_bytes(b"not found", "text/plain", 404)
            if path.startswith("/revev/"):          # the contact sheet of a drive review event: /revev/<drive stamp>/<event>.jpg
                p = safe_join(os.path.join(DATA_DIR, "drive_review", "events"), path[7:])
                if p and os.path.isfile(p) and p.endswith(".jpg"):
                    return self.send_file(p, cache=True)
                return self.send_bytes(b"not found", "text/plain", 404)
            if path.startswith("/footage/"):
                role, _, rel = path[9:].partition("/")
                root = footage_root(role)
                if not root or not rel.lower().endswith(FOOT_EXT):
                    return self.send_bytes(b"drive not connected", "text/plain", 404)
                p = safe_join(root, rel)
                if p and os.path.isfile(p):
                    if KEEP:
                        KEEP.playing_now()          # the background reading steps aside while video plays
                    return self.send_file(p, cache=True)
                return self.send_bytes(b"not found", "text/plain", 404)
            return self.send_bytes(b"not found", "text/plain", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:      # never kill the server on one bad request
            try:
                self.send_json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path in ("/api/app/quit", "/api/app/restart"):      # quit the app, or start it again with the program as it is on disk
                return self.send_json(app_exit(u.path.endswith("restart")))
            if u.path == "/api/places/save":
                with _ACTION_LOCK:
                    return self.send_json(STORE.pl.save(body))
            if u.path == "/api/places/delete":
                with _ACTION_LOCK:
                    if body.get("confirm") is not True:
                        return self.send_json(dict(ok=False, error="Not confirmed: nothing was deleted."))
                    return self.send_json(STORE.pl.delete(body.get("name", "")))
            if u.path == "/api/drivereview":       # {id, action: start|stop}: the manual review of one drive
                if body.get("action") == "stop":
                    DRV.stop()
                else:
                    DRV.start(body.get("id", ""), footage_roots(), {})
                return self.send_json(drive_review_state(body.get("id", "")))
            if u.path == "/api/review":
                role = body.get("role", "A")
                if body.get("action") == "redo" and body.get("confirm") is not True:         # reading again forgets what was read: ask first
                    return self.send_json(dict(error="Not confirmed: nothing was changed."))
                return self.send_json(TC.review(role, footage_root(role), _utc_arg(body.get("t0", "")), _utc_arg(body.get("t1", "")), body.get("action", "start"), _lo_arg(body.get("lo"))))
            if u.path == "/api/keeper":            # {action: pause|resume|run}
                act = body.get("action")
                return self.send_json(KEEP.pause() if act == "pause" else KEEP.run_now(body.get("stages")) if act == "run" else KEEP.resume())
            if u.path == "/api/clips/act":         # {action: delete|move|process|stop, role, items: [{kind, id}]}: remove the video of clips from a drive (after it is all kept), move it to the Archive, or read what the clips still lack from their video
                if body.get("action") == "stop":
                    return self.send_json(CKEEP.stop())
                held = KEEP.stand_down()           # the Keeper's own pass gives way to a button pressed here
                if held:
                    return self.send_json(dict(error="%s is reading the drives: stop it first." % held))
                role = body.get("role")
                roots = footage_roots()
                if body.get("action") == "process":       # read what the clips still lack (metadata, stills, pictures, signs, or only `steps`) from their video on the connected drives
                    return self.send_json(CKEEP.start_process(body.get("items") or [], roots, steps=body.get("steps")))
                if body.get("action") == "purge":         # delete chosen kinds (video, metadata, stills, pictures, signs) of the clips; video that is not all kept needs force
                    return self.send_json(CKEEP.purge(body.get("items") or [], body.get("types") or [], roots, confirmed=body.get("confirm") is True, force=body.get("force") is True))
                return self.send_json(CKEEP.start(body.get("action"), role, body.get("items") or [], roots, confirmed=body.get("confirm") is True))
            if u.path == "/api/stills/thin":       # {action: start|stop, events: normal|shrunk|front}: make the stills of minutes with no event and no sign smaller, or the pictures around events
                if body.get("action") == "stop":
                    return self.send_json(STHIN.stop())
                clips, stamps = thin_scope(body.get("clips"))
                return self.send_json(STHIN.start(confirmed=body.get("confirm") is True, events=body.get("events") or None, clips=clips, stamps=stamps))
            if u.path == "/api/clipfiles":         # the videos on screen as files: {action: reveal|copy|status|open, urls, name, job}
                act = body.get("action")
                if act == "status":
                    return self.send_json(COPIER.status(str(body.get("job", ""))))
                if act == "open":                  # the folder a finished copy went to
                    return self.send_json(clip_files.open_folder(COPIER.dest_of(str(body.get("job", "")))))
                paths = clip_files.resolve(body.get("urls"), footage_root, safe_join)
                if act == "reveal":
                    return self.send_json(clip_files.reveal(paths))
                if act == "copy":
                    if not paths:
                        return self.send_json(dict(ok=False, error="None of those videos is on a connected drive."))
                    folder = clip_files.choose_folder("Copy %d video%s (%.1f GB) to which folder?" % (len(paths), "" if len(paths) == 1 else "s", sum(os.path.getsize(p) for p in paths) / 1e9))
                    if not folder:
                        return self.send_json(dict(ok=False, cancelled=True))
                    return self.send_json(COPIER.start(paths, folder, str(body.get("name") or "TeslaCam clips")))
                return self.send_json(dict(ok=False, error="Unknown action."))
            if u.path == "/api/eject":             # {role}: stop reading the drive, then eject it
                role = body.get("role")
                root = footage_root(role) if role in ("A", "J", "U") else None
                if not root:
                    return self.send_json(dict(ok=False, text="That drive is not connected."))
                return self.send_json(eject_drive(role, root))
            if u.path == "/api/footage":           # {action: log|stills|stop, roles: ["A","J"]}: update the log of the files / make the missing one-second stills
                if body.get("action") == "stop":
                    return self.send_json(FG.stop())
                roles = [r for r in (body.get("roles") or ["A"]) if r in DR.read_roles()]
                return self.send_json(FG.start("stills" if body.get("action") == "stills" else "log", {r: footage_root(r) for r in roles}))
            if u.path == "/api/scan":              # {action: start|stop, roles: ["A","J"]}: read the telemetry the picked drives still lack
                if body.get("action") == "stop":
                    return self.send_json(TC.scan_stop())
                roles = [r for r in (body.get("roles") or ["A"]) if r in DR.read_roles()]
                return self.send_json(TC.scan_start({r: footage_root(r) for r in roles}))
            if u.path == "/api/reach":             # {pts, mode, frac, reserve, basis, bbox, size}: a picture to lay over the Places map
                return self.send_json(REACH.compute(body))
            if u.path == "/api/reach/at":          # {pts, lat, lon, basis, frac, reserve}: the nearest chargers on the roads from a spot
                return self.send_json(REACH.at(body))
            if u.path == "/api/reach/build":
                return self.send_json(REACH.start_build())
            if u.path == "/api/chargers/sync":
                return self.send_json(SYNC.start())
            if u.path == "/api/chargers/sync/stop":
                return self.send_json(SYNC.stop())
            if u.path == "/api/tessie/sync":
                if not app_sync.tessie_configured():
                    return self.send_json(dict(ok=False, error="Tessie is not set up: add the access token and VIN to config.json (tessie.access_token, tessie.vin)."))
                if urllib.parse.parse_qs(u.query).get("kind", [""])[0] == "charges":
                    r = CSYNC.start()
                    if r.get("ok") is not False and r.get("running"):
                        charges_started()
                    return self.send_json(r)
                r = TSYNC.start()
                if r.get("ok") is not False and r.get("running"):
                    tessie_started()
                return self.send_json(r)
            if u.path == "/api/charges/send-costs":       # write each charge's cost (Home: the scripts' cost; public: invoice share) to Tessie where it differs
                if not app_sync.tessie_configured():
                    return self.send_json(dict(ok=False, error="Tessie is not set up: add the access token and VIN to config.json (tessie.access_token, tessie.vin)."))
                if CSYNC.running():
                    return self.send_json(dict(ok=False, error="The Tessie sync is running; try again when it has finished."))
                return self.send_json(CCOST.start())
            if u.path == "/api/charges/send-costs/stop":
                return self.send_json(CCOST.stop())
            if u.path == "/api/tessie/sync/stop":
                return self.send_json((CSYNC if urllib.parse.parse_qs(u.query).get("kind", [""])[0] == "charges" else TSYNC).stop())
            if u.path == "/api/software/sync":           # {since: 2026}: fetch what Teslascope has that is not held yet (lib/software/sync.py), in the background
                return self.send_json(SOFT.start(body.get("since")))
            if u.path == "/api/software/sync/stop":
                return self.send_json(SOFT.stop())
            if u.path == "/api/queue/run":
                return self.send_json(run_queue(body))
            return self.send_bytes(b"not found", "text/plain", 404)
        except Exception as e:
            self.send_json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)

    def software(self, name, q):
        g = lambda k, d="": (q.get(k, [d])[0] or d)
        region, model, hw = g("region", "ANZ"), g("model") or None, g("hw") or None
        try:
            if name == "solaredge":
                from lib import solaredge_v2 as SE2
                return SE2.status()
            if name == "software/sync":
                return SOFT.status()
            if name == "software/version":
                return app_software.detail(DATA_DIR, g("v"), region, model, hw)
            if name == "software/timeline":
                return app_software.timeline(DATA_DIR, region, model, hw)
            if name == "software/first":
                return app_software.first(DATA_DIR, g("q"), region)
            if name == "software/compare":
                return app_software.compare(DATA_DIR, g("a"), g("b"), region, model, hw)
            return app_software.overview(DATA_DIR, region, model, hw)
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e)}

    def api(self, name, q):
        S = STORE
        if name in KEPT_LISTS:                  # answered at once from what was kept, refreshed behind (lib/app_kept.py); ?fresh=1 waits for the newest
            raw, stale = KEPT.get(name, lambda: KEPT_LISTS[name](), q.get("fresh", [""])[0] == "1")
            return self.send_bytes(raw, "application/json; charset=utf-8", 200, {"X-Stale": "1"} if stale else None)
        if name == "status":
            try:
                known = sorted({d["role"] for d in DR.REG.drives() if d["role"] in DR.read_roles()} | {"A"})      # the registered drives stay in the list while they are away
            except Exception:
                known = ["A"]
            return self.send_json(dict(roots={r: bool(footage_root(r)) for r in DR.read_roles()}, known=known, names={d["role"]: d["label"] for d in DR.REG.drives() if d["role"] in DR.read_roles()}, data_dir=DATA_DIR, stale=code_updated(), ui=ui_stamp()))
        if name.startswith("software"):         # software[/version|/first|/compare|/sync]: the Teslascope versions held (lib/app_software.py)
            return self.send_json(self.software(name, q))
        if name == "keeper":                    # what the background reading of the connected drives is doing
            return self.send_json(KEEP.status())
        if name == "sentry":
            ev, ok = all_sentry()
            return self.send_json(dict(connected=ok, events=ev))
        if name == "review":                    # review?role=A&t0=<UTC epoch s>&t1=<UTC epoch s>: signs read so far + the job
            role = q.get("role", ["A"])[0]
            try:
                return self.send_json(TC.review(role, footage_root(role), _utc_arg(q.get("t0", [""])[0]), _utc_arg(q.get("t1", [""])[0]), "status", _lo_arg(q.get("lo", [None])[0])))
            except ValueError:
                return self.send_json({"error": "bad time"}, 400)
        if name == "drivefuse":                 # drivefuse?id=<drive folder>[&summary=1]: the Tessie drive's track, finer where dashcam telemetry exists
            r = TC.fuse(q.get("id", [""])[0])
            if not r:
                return self.send_json({"error": "no sample file for this drive"}, 404)
            return self.send_json(dict(info=r["info"]) if q.get("summary") else r)
        if name == "scan":                      # scan?roles=A,J: the footage on those drives against the telemetry kept (nothing is read)
            roles = [r for r in q.get("roles", ["A"])[0].split(",") if r in DR.read_roles()]
            return self.send_json(TC.scan_report({r: footage_root(r) for r in roles}))
        if name == "footage/log":               # the log of every TeslaCam file as it stands
            return self.send_json(FG.log())
        if name == "footage/stills":            # footage/stills?roles=A,J: the front clips on those drives against the stills kept (nothing is made)
            roles = [r for r in q.get("roles", ["A"])[0].split(",") if r in DR.read_roles()]
            return self.send_json(FG.stills_report({r: footage_root(r) for r in roles}))
        if name == "footage/todo":              # what each connected drive still needs: files not in the log, minutes without telemetry, clips without stills
            return self.send_json(TODO.snapshot())
        if name == "footage/status":
            return self.send_json(FG.status())
        if name == "evstills":                  # evstills?kind=saved|sentry&id=<event folder>: the pictures kept around the event: camera, level and the seconds of each minute
            kind, eid = q.get("kind", [""])[0], q.get("id", [""])[0]
            if kind not in ("saved", "sentry") or not re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", eid):
                return self.send_json({})
            i = CKEEP.evst.info(kind, eid)
            es = CKEEP.evst
            return self.send_json(dict(cam=i.get("cam"), tier=i.get("tier"), width=i.get("width"), trigger=i.get("trigger"), secs=es.seconds(kind, eid),
                                       cams=es.cams(kind, eid), others={c: es.seconds(kind, eid, c) for c in es.cams(kind, eid) if c != i.get("cam")},
                                       half=es.seconds(kind, eid, half=True) if es.halves(kind, eid) else {}) if i.get("n") else {})
        if name == "signs":                     # signs?stamps=a,b: the signs read in those minutes {clip stamp: [sign]} (lib/app_review.py minute_signs)
            return self.send_json(TC._rv().minute_signs([s for s in q.get("stamps", [""])[0].split(",") if re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", s)][:80]))
        if name == "schoolday":                 # schoolday?date=YYYY-MM-DD&lat=&lon=: is that a school day in the state of that spot? {state, school_day: true / false / null (no calendar for that state or year)}
            try:
                dt = q.get("date", [""])[0]
                st = GEO.state_from_coords(float(q.get("lat", [""])[0]), float(q.get("lon", [""])[0]))[0]
                return self.send_json(dict(state=st, school_day=SD.school_day(st, dt) if st else None))
            except (ValueError, TypeError):
                return self.send_json(dict(state=None, school_day=None))
        if name == "clipstatus":                # clipstatus?stamps=a,b: kept on a drive / deleted / never logged, and whether its stills are kept
            return self.send_json(FG.clip_status([s for s in q.get("stamps", [""])[0].split(",") if re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", s)][:80]))
        if name == "clipholders":               # clipholders?stamps=a,b: the files of each minute and the drives that hold them, biggest drive first, connected or not
            return self.send_json(FG.clip_holders([s for s in q.get("stamps", [""])[0].split(",") if re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", s)][:80], [r for r in DR.read_roles() if footage_root(r)]))
        if name == "scan/status":
            return self.send_json(TC.scan_status())
        if name == "clipclock":                 # the car's clock as learned from the footage (clip_clock.py): runs of minutes with the offset from UTC
            return self.send_json(CC.status())
        if name == "drivereview/all":           # {drive: percent of its events' stills looked at for signs}: the Reviewed column of the drives list
            return self.send_json(DRV.reviewed())
        if name == "drivereview":               # drivereview?id=<drive folder>: its last review, the job state, the default speeds
            return self.send_json(drive_review_state(q.get("id", [""])[0]))
        if name == "review/scanned":            # every minute whose signs have been read: {UTC epoch s of the minute: signs found}
            return self.send_json(TC.review_scanned())
        if name == "teslacam":                  # the drives that are connected right now (found by what is on them)
            _ROOTS["t"] = 0
            return self.send_json(dict(drives=TC.drives()))
        if name == "drivelist":                 # the Drives list: Tessie's drives and charges as the rows, the dashcam's drives attached (or alone when there is no Tessie)
            return self.send_json(drivelist())
        if name == "stills/thin":               # how many minutes of ordinary driving have stills that could be smaller, what they take, and the job
            clips, stamps = thin_scope([dict(kind=x.partition("|")[0], id=x.partition("|")[2]) for x in q.get("clips", [""])[0].split(",") if "|" in x])
            return self.send_json(STHIN.scope_status(sorted(clips), stamps) if clips else STHIN.status())
        if name == "clips/keep":                # per clip (Recent run, Saved, Sentry): what is kept from its video, and its size on each drive (nothing is read from a drive)
            return self.send_json(CKEEP.status())
        if name == "clips/job":                 # the delete / move being done to the drives, or the last one
            return self.send_json(CKEEP.job())
        if name == "clips/history":             # what the last deletes and moves removed (kept across restarts)
            return self.send_json(dict(jobs=CKEEP.history(30)))
        if name == "clips":                     # every Recent run, Saved and Sentry event ever logged, whichever drives are connected; each says which drive holds it and whether it can be played
            if DRIVELIST.has():                 # the cuts between the drive and parked sections of a recording are worked out (kept from the last time): refreshed behind, never waited for
                drivelist()
            return self.send_json(TC.clips_all(footage_roots()))
        if name.startswith("clips/shots/"):     # clips/shots/<saved|sentry>/<event folder>: the cameras that have a full-size picture of the moment, and which second of which clip it is
            m = re.match(r"^clips/shots/(saved|sentry)/(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$", name)
            if not m:
                return self.send_json(dict(cams=[]))
            return self.send_json(dict(CKEEP.shots.info(m.group(1), m.group(2)), cams=sorted(CKEEP.shots.have(m.group(1), m.group(2)))))
        if name.startswith("clips/after/"):     # clips/after/<minute>?n=&skip=<minutes already shown, comma separated>: the continuous recording that follows an event
            try:
                n = max(0, min(60, int(q.get("n", ["10"])[0])))
            except ValueError:
                n = 10
            skip = [x for x in (q.get("skip", [""])[0] or "").split(",") if x]
            return self.send_json(TC.after_all(urllib.parse.unquote(name[12:]), n, footage_roots(), skip))
        if name.startswith("clips/run/"):       # clips/run/<first minute>: the minutes of one Recent run, with the drive each can be played from
            d = TC.run_all(urllib.parse.unquote(name[10:]), footage_roots())
            return self.send_json(d if d else {"error": "No such recording."}, 200 if d else 404)
        if name.startswith("teslacam/"):        # teslacam/<role>/<sentry|saved|recent>[/<run>]
            role, _, rest = name[9:].partition("/")
            kind, _, rid = rest.partition("/")
            root = footage_root(role)
            if kind == "seg":
                sid = urllib.parse.unquote(rid)
                if role == "auto":                  # whichever connected drive holds the footage of this drive (the Drives list does not know which drive it is on)
                    try:
                        t = app_teslacam.TL.secs(sid)
                    except Exception:
                        t = 0
                    role, root = TC.pick_role(footage_roots(), t - 60, t + 60)
                if not root:
                    return self.send_json({"error": "The drive holding this footage is not connected."}, 404)
                d = TC.segment(role, root, sid, float(q.get("budget", ["20"])[0] or 20))
                if d:
                    d["role"] = role
                return self.send_json(d if d else {"error": "TeslaCam drive %s is connected, but it has no recording starting at this time." % role}, 200 if d else 404)
            if kind == "window":                    # teslacam/<role>/window?t0=<UTC epoch s>&t1=<UTC epoch s>[&lo=<display offset s>][&budget=s]
                try:
                    t0 = _utc_arg(q.get("t0", [""])[0])
                    t1 = _utc_arg(q.get("t1", [""])[0])
                except ValueError:
                    return self.send_json({"error": "bad time"}, 400)
                if role == "auto":                  # whichever connected drive holds the footage for these times
                    role, root = TC.pick_role(footage_roots(), t0, t1)
                lo_arg = _lo_arg(q.get("lo", [None])[0])
                d = TC.window(role, root, t0, t1, float(q.get("budget", ["20"])[0] or 20), lo_arg, q.get("sentry", [""])[0] or None)
                tid = q.get("id", [""])[0]
                if tid:                             # the Drives list: Tessie's drive is the base, so its own route fills what the dashcam's telemetry does not cover
                    d = tessie_filled(d, tid, t0, t1, lo_arg)
                if d:
                    d["role"] = role
                return self.send_json(d if d else {"error": "no footage or telemetry for this time"}, 200 if d else 404)
            if kind == "recent" and rid:
                c = TC.run_clips(role, root, urllib.parse.unquote(rid))
                return self.send_json(dict(clips=c, loc=TC.loc_of(c)) if c else {"error": "no such run"}, 200 if c else 404)
            if kind == "recent":
                runs, ok = TC.recent(role, root)
                return self.send_json(dict(connected=ok, runs=runs))
            ev, ok = TC.events(role, kind, root)
            return self.send_json(dict(connected=ok, events=ev))
        if name == "chargebook":
            return self.send_json(BOOK.get())
        if name.startswith("chargebook/"):
            d = BOOK.detail(name[11:], q.get("part", [""])[0] or None)
            return self.send_json(d if d else {"error": "no such charge"}, 200 if d else 404)
        if name.startswith("invoice/"):
            f = BOOK.invoice_file(urllib.parse.unquote(name[8:]))
            return self.send_file(f) if f else self.send_bytes(b"invoice file not found", "text/plain", 404)
        if name.startswith("charge/"):
            d = S.charge_detail(name[7:])
            return self.send_json(d if d else {"error": "no such charge"}, 200 if d else 404)
        if name.startswith("idle/"):
            i = next((x for x in S.idles() if x["id"] == name[5:]), None)
            return self.send_json(i if i else {"error": "no such idle"}, 200 if i else 404)
        if name == "pathsources":                       # where the line on a drive's map comes from: Tessie's point file and the dashcam's telemetry (and any other drive's file that only touches this one)
            try:
                t0, t1 = _utc_arg(q.get("t0", [""])[0]), _utc_arg(q.get("t1", [""])[0])
            except ValueError:
                return self.send_json({"error": "bad time"}, 400)
            stamp, lo_arg = q.get("id", [""])[0], _lo_arg(q.get("lo", [None])[0])
            root = os.path.join(DATA_DIR, "drives", "deepdive")
            fp = DP.find_data(root, stamp) if stamp else None
            tess = dict(file=os.path.relpath(fp, DATA_DIR) if fp else None, **(app_route.describe(app_route.points(fp, lo_arg), t0, t1) if fp else dict(points=0)))
            near = []
            for j in STORE._summaries():
                try:
                    a0, a1 = float(j["t0"]), float(j["t1"])
                except (KeyError, TypeError, ValueError):
                    continue
                ov = min(a1, t1) - max(a0, t0)
                if j.get("stamp") != stamp and ov > 0:
                    near.append(dict(stamp=j.get("stamp"), overlap_s=round(ov), start=j.get("start"), end=j.get("end")))
            return self.send_json(dict(tessie=tess, dashcam=TC.path_sources(t0, t1), others=near[:6], t0=t0, t1=t1, has_page=bool(fp), fetched=FetchLedger(DATA_DIR).get("drive_path", "t%d" % int(t0))))
        if name == "tessie" and q.get("kind", [""])[0] == "charges":
            return self.send_json(dict(configured=app_sync.tessie_configured(), synced_at=charges_state().get("at"), fresh_minutes=app_config.get("drives.tessie_fresh_minutes"), **{k: v for k, v in CSYNC.status(0).items() if k != "lines"}))
        if name == "tessie":
            return self.send_json(dict(configured=app_sync.tessie_configured(), auto_minutes=app_config.get("drives.tessie_auto_sync_minutes"), synced_at=tessie_state().get("at"), fresh_minutes=app_config.get("drives.tessie_fresh_minutes"), **{k: v for k, v in TSYNC.status(0).items() if k != "lines"}))
        if name == "charges/send-costs":
            return self.send_json(CCOST.status(int(q.get("since", ["0"])[0] or 0)))
        if name == "tessie-sync":
            return self.send_json((CSYNC if q.get("kind", [""])[0] == "charges" else TSYNC).status(int(q.get("since", ["0"])[0] or 0)))
        if name == "reach":
            return self.send_json(REACH.status())
        if name == "chargers-sync":
            return self.send_json(SYNC.status(int(q.get("since", ["0"])[0] or 0)))
        if name == "settings":
            return self.send_json(app_config.settings())
        if name == "place":
            d = S.pl.detail(S, q.get("key", [""])[0], q.get("name", [""])[0], q.get("addr", [""])[0])
            return self.send_json(d if d else {"error": "no such place"}, 200 if d else 404)
        if name == "site":                       # what the charger registry knows about one charging site (the map's charger card)
            d = S.pl.site(q.get("key", [""])[0])
            return self.send_json(d if d else {"error": "no such site"}, 200 if d else 404)
        if name == "place-osm":
            g = lambda k, dv="": q.get(k, [dv])[0]
            try:
                return self.send_json(S.pl.osm(g("key"), float(g("lat")), float(g("lon")), int(float(g("r", "300"))), g("refresh") == "1"))
            except ValueError:
                return self.send_json({"error": "bad coordinates"}, 400)
        return self.send_bytes(b"not found", "text/plain", 404)


def run_queue(body):
    """Run the footage actions queued on the drive pages (lib/footage_actions.py, as drive_actions.py does). apply=False only describes what would happen."""
    import footage_actions as DA
    actions = body.get("actions") or []
    apply = bool(body.get("apply"))
    if apply and any(a.get("type") == "delete" for a in actions) and body.get("confirm") is not True:
        return {"ok": False, "output": "Not confirmed: nothing was deleted."}
    buf = io.StringIO()
    with _ACTION_LOCK, redirect_stdout(buf):
        print("%s\n" % ("APPLYING" if apply else "PREVIEW - nothing is changed"))
        ok = True
        try:
            DA.run_actions(actions, apply)
            if body.get("purge") and any(a.get("type") == "delete" for a in actions):
                DA.run_purge(apply)
        except Exception as e:
            ok = False
            print("\n! stopped: %s: %s" % (type(e).__name__, e))
    return {"ok": ok, "output": buf.getvalue()}


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        """A page that moved on drops its requests half way (a connection reset or broken pipe): not an error, and it filled the log with 15,000 tracebacks."""
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def serve(port, host="127.0.0.1"):
    srv = Server((host, port), H)
    srv.daemon_threads = True
    return srv


def tessie_filled(d, stamp, t0, t1, lo):
    """The window's series stretched over the whole Tessie drive [t0, t1] and filled from Tessie's deep-dive points between the dashcam's seconds (nothing changes without that file)."""
    pts = []
    for one in str(stamp).split(","):                  # a drive joined from several of Tessie's (comma between their ids): the points of every part, once
        csv_path = DP.find_data(os.path.join(DATA_DIR, "drives", "deepdive"), one)
        pts += app_route.points(csv_path, lo) if csv_path else []
    pts = sorted(set(pts))
    if not pts:
        return d
    ser = app_route.fill((d or {}).get("series"), t0, t1, pts)
    TLm = app_teslacam.TL
    if d is None:
        d = dict(item=dict(lo=lo if lo is not None else 0), connected=False, minutes=[], pending=0, events=[])
    d["series"] = ser
    d["item"] = dict(d["item"], t0=ser["t0"], start=TLm.iso(ser["t0"], d["item"].get("lo") or 0))
    return d


def tessie_state():
    try:
        with open(os.path.join(DATA_DIR, "tessie_sync_state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def charges_state():
    try:
        with open(os.path.join(DATA_DIR, "tessie_charges_state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def charges_started():
    """The Charges page started its Tessie sync: when it ends cleanly the time is kept, so the page can say how fresh the charges are."""
    def after():
        while CSYNC.running():
            time.sleep(1)
        if CSYNC.status(0).get("rc") != 0:
            return
        try:
            with open(os.path.join(DATA_DIR, "tessie_charges_state.json"), "w", encoding="utf-8") as f:
                json.dump(dict(at=time.time()), f)
        except OSError:
            pass
    threading.Thread(target=after, daemon=True).start()


def tessie_started():
    """A Tessie sync was started: when it ends cleanly, dashcam-only drives that were missing then and are still missing are what Tessie has no record of, so the page stops offering to fetch them (Tessie's own drives record their path outcome in FetchLedger when asked)."""
    ids = [i for i in ((drivelist().get("missing") or {}).get("ids") or []) if not str(i).startswith("t")]

    def after():
        while TSYNC.running():
            time.sleep(1)
        if TSYNC.status(0).get("rc") != 0:
            return
        still = set((drivelist(True).get("missing") or {}).get("ids") or [])      # rebuilt with what the sync brought
        tried = sorted(still & set(ids) | set(tessie_state().get("tried") or []))
        try:
            with open(os.path.join(DATA_DIR, "tessie_sync_state.json"), "w", encoding="utf-8") as f:
                json.dump(dict(tried=tried, at=time.time()), f)
        except OSError:
            pass
    threading.Thread(target=after, daemon=True).start()


def local_cache_dir():
    """A folder on this computer, outside the data folder (which iCloud shares between the Macs)."""
    import hashlib
    base = os.path.expanduser("~/Library/Caches/Tesellate" if sys.platform == "darwin" else "~/.cache/tesellate")
    return os.path.join(base, hashlib.md5(os.path.abspath(DATA_DIR).encode()).hexdigest()[:8])


def drivelist(wait=False):
    """The merged Drives list (Tessie's drives and charges with the dashcam's attached), rebuilt only when a file it is built from changed."""
    def build():
        act = STORE.activity()
        tess = [x for x in act if x["k"] == "drive"]
        cam = TC.timeline_stored(None)["items"]
        moves = [dict(t0=x["t0"], t1=x["t1"], p0=[x.get("lat"), x.get("lon")], p1=[x.get("lat1"), x.get("lon1")]) for x in tess if x.get("t0") and x.get("t1")] or None       # Tessie's drives, when there are any: where the car went when no clip shows it
        upto = float(tessie_state().get("at") or 0)
        marks = {}
        try:
            cam = TLN.fill_places(cam, moves, upto)         # the stops the dashcam made while parked have no GPS: the place comes from the drive beside them, Tessie's drives, or the last known place
            cam.sort(key=lambda x: x["t0"], reverse=True)
        except Exception:
            pass
        try:
            cams, holders, dirs = TC.all_minutes()          # Recent minutes and the minutes the car saved off into Saved / Sentry folders: one clock
            store, roots = TC._tl("A"), footage_roots()
            front = lambda st: next((os.path.join(roots[r], *dirs.get(st, "RecentClips").split("/"), st + "-front.mp4") for r in sorted(holders.get(st) or ()) if roots.get(r) and "front" in (cams.get(st) or ())), None)
            minute_kind.probe_bg(KINDS, [st for st in cams if store.summary(st) is None], front)          # minutes whose telemetry is not read yet are told by walking their clip, once, all of them, in the background (the file it keeps changing rebuilds this list)
            looked = KINDS.all()
            kinds = {}
            for st in cams:                                  # what each minute's own clip says: in gear or moving, Park at 0 km/h with telemetry, or no telemetry
                sm = store.summary(st)
                c = looked.get(st) if sm is None else "" if not isinstance(sm, dict) else "go" if sm.get("mov") else "pk"
                if c is not None:
                    kinds[st] = c
            marks = {}
            for st, c in kinds.items():                      # a stop in Park inside the driving (a drop-off) is read from the gear of the minute's telemetry
                if c == "go":
                    try:
                        ser = store.series(st)
                        if ser and ser.get("gear"):
                            marks[st] = run_cuts.stop_marks(ser["gear"])
                    except Exception:
                        pass
            run_cuts.update(cams, cam, moves, upto, kinds, marks)         # where a Recent recording is cut into its drive and parked sections (the TeslaCam list): the clips first, Tessie only aligns an edge
        except Exception:
            pass
        try:                                             # the dashcam's own drives and parks meet at the second Park was engaged, not at the edge of a clip's minute (Tessie's rows are already to the second)
            if marks:
                cam = run_cuts.refine_edges(cam, marks, {int(round(CC.utc(st))): st for st in marks})
                cam.sort(key=lambda x: x["t0"], reverse=True)
        except Exception:
            pass
        items = app_drivelist.merge(tess, [x for x in act if x["k"] == "charge"], cam, [x for x in act if x["k"] == "idle"])
        return dict(items=items, tessie=bool(tess), **app_drivelist.page_rows(act), missing=app_drivelist.missing(items, time.time(), tried=set(tessie_state().get("tried") or []) | FetchLedger(DATA_DIR).settled("drive_path")))
    d = os.path.join
    watch = [d(DATA_DIR, "drives", "drives_master.csv"), d(DATA_DIR, "charges", "charges_master.csv"), d(DATA_DIR, "drives", "deepdive"), d(DATA_DIR, "tessie_sync_state.json"), d(DATA_DIR, "fetch_ledger.json"), d(DATA_DIR, "clip_clock.json"), d(DATA_DIR, "places.json"), d(DATA_DIR, "teslacam_timeline", "index.json"),
             d(DATA_DIR, "teslacam_ledger"), d(DATA_DIR, "teslacam_provenance"), d(DATA_DIR, "teslacam_timeline", "index.json"), KINDS.path] + [app_sentry.index_path(DATA_DIR, "teslacam_index_%s_%s.json" % (r, k)) for r in DR.read_roles() for k in ("saved", "sentry")]
    return DRIVELIST.get(app_drivelist.signature(watch), build, wait)


def tessie_auto():
    """With app.drives.tessie_auto_sync_minutes above 0 and Tessie set up: when the dashcam saw drives Tessie has not given yet, run the Drives sync, at most that often."""
    last = 0.0
    while True:
        time.sleep(60)
        try:
            gap = float(app_config.get("drives.tessie_auto_sync_minutes") or 0) * 60
            if gap <= 0 or TSYNC.running() or time.time() - last < gap or not app_sync.tessie_configured():
                continue
            m = drivelist(True).get("missing") or {}
            if m.get("cam_only") or m.get("no_page"):
                last = time.time()
                TSYNC.start()
        except Exception as e:
            print("(Tessie auto sync: %s)" % e, flush=True)


def fuel_auto():
    """Every app.fuel.auto_sync_hours (default 12; 0 = never): when last month's FuelCheck prices are not held yet, fetch them (lib/fuel_auto.py)."""
    import fuel_auto
    time.sleep(120)
    while True:
        try:
            hours = float(app_config.get("fuel.auto_sync_hours") if app_config.get("fuel.auto_sync_hours") is not None else 12)
            if hours > 0 and not FSYNC.running() and fuel_auto.due(DATA_DIR):
                FSYNC.start()
        except Exception as e:
            print("(fuel auto sync: %s)" % e, flush=True)
        time.sleep(max(1.0, hours if hours > 0 else 12) * 3600)


def _warm(port):
    """Asks this server for the pages' data once, in the background, so the first page the window opens is not the one that waits for everything to load (the first
    request after a start builds the lists; later ones answer from memory). Only what is kept here: nothing is read from a drive."""
    import urllib.request
    time.sleep(8)                   # the window's first page goes first; what it asks for is answered from what was kept
    for path in ("summary", "activity", "drivelist", "places", "alerts", "chargers", "battery", "analytics", "teslacam", "clips", "clips/keep"):      # the charge book is looked at only when the Charges page asks for it
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/%s" % (port, path), timeout=300).read()
        except Exception:
            pass


WIN = None                  # the app's window (pywebview), when it has one
_WGEO = {}
_WGEO_T = [None]


def _wgeo_path():
    return os.path.join(local_cache_dir(), "window.json")


def window_place(webview):
    """Size and place of the window as it was when last moved or resized (kept on this computer); the configured size the first time,
    and the configured size in the default place when the remembered place is on no screen now (a display unplugged)."""
    out = dict(width=int(app_config.get("window.width")), height=int(app_config.get("window.height")))
    try:
        with open(_wgeo_path(), encoding="utf-8") as f:
            g = json.load(f)
    except (OSError, ValueError):
        return out
    _WGEO.update(g if isinstance(g, dict) else {})
    if _WGEO.get("w", 0) >= 900 and _WGEO.get("h", 0) >= 600:
        out.update(width=int(_WGEO["w"]), height=int(_WGEO["h"]))
    x, y = _WGEO.get("x"), _WGEO.get("y")
    if x is not None and y is not None:
        try:
            scr = list(webview.screens or [])
        except Exception:
            scr = []
        if not scr or any(sc.x - 50 <= x < sc.x + sc.width - 100 and sc.y - 50 <= y < sc.y + sc.height - 100 for sc in scr):
            out.update(x=int(x), y=int(y))
    return out


def window_keep(**kw):
    """The window was moved or resized: remembered a moment later (a drag sends many of these)."""
    _WGEO.update({k: int(v) for k, v in kw.items() if v is not None})
    if _WGEO_T[0]:
        _WGEO_T[0].cancel()

    def save():
        try:
            os.makedirs(os.path.dirname(_wgeo_path()), exist_ok=True)
            with open(_wgeo_path() + ".tmp", "w", encoding="utf-8") as f:
                json.dump(_WGEO, f)
            os.replace(_wgeo_path() + ".tmp", _wgeo_path())
        except OSError:
            pass
    _WGEO_T[0] = threading.Timer(1.0, save)
    _WGEO_T[0].daemon = True
    _WGEO_T[0].start()


def app_exit(restart=False):
    """Quit the app, or quit and start it again (the new start loads the program as it is on disk now). From the app's own window
    (started by Tesellate.app) the restart opens the app again by its bundle id once this one has gone; started from a terminal it
    starts this same command again in place."""
    def go():
        time.sleep(0.4)                     # the answer reaches the page first
        bundled = os.environ.get("__CFBundleIdentifier") == "net.inodes.tesellate"
        if restart and bundled:
            subprocess.Popen(["/bin/sh", "-c", "sleep 1.5; open -b net.inodes.tesellate"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if restart and not bundled:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        try:
            if WIN is not None:
                WIN.destroy()
        except Exception:
            pass
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()
    return dict(ok=True)


KEPT = None
KEPT_LISTS = {          # the lists a page asks for first: answered from what was kept and refreshed behind (lib/app_kept.py)
    "summary": lambda: STORE.summary(),
    "activity": lambda: STORE.activity(),
    "battery": lambda: dict(readings=STORE.battery(), spec=STORE.spec()),
    "analytics": lambda: STORE.analytics(),
    "alerts": lambda: STORE.alerts(),
    "places": lambda: STORE.pl.listing(STORE),
    "chargers": lambda: STORE.pl.charger_listing(STORE),
}


def _code_version():
    """Changes when the program changes, so a list kept by older code is never shown by newer code."""
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    return str(int(max([os.path.getmtime(f) for f in glob.glob(os.path.join(here, "*.py")) + glob.glob(os.path.join(here, "lib", "*.py")) + glob.glob(os.path.join(here, "commands", "*.py"))] or [0])))


def main():
    global KINDS, KEPT, STORE, DATA_DIR, SENTRY, TC, DRV, SYNC, TSYNC, CSYNC, CCOST, FSYNC, REACH, SOFT, CARW, BOOK, FG, KEEP, DRIVELIST, TODO, CKEEP, STHIN
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--port", type=int, default=int(app_config.get("window.port")))
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--no-background", action="store_true", help="do not read the connected TeslaCam drives in the background (see app.keeper)")
    ap.add_argument("--browser", action="store_true")
    a, extra = ap.parse_known_args()
    if extra:
        print("(ignoring extra arguments: %s - put comments on their own line, not after the command)" % " ".join(extra))
    DATA_DIR = os.path.expanduser(a.data_dir)
    if not os.path.isdir(DATA_DIR):
        print("Data folder not found: %s (use --data-dir)" % DATA_DIR)
        return 1
    repo = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    here = os.path.realpath(DATA_DIR)
    if here == repo or here.startswith(repo + os.sep):
        print("The data folder is inside the code folder (%s): no data is kept in the repository. Use --data-dir with the data folder." % repo)
        return 1
    try:
        config_backup.backup(app_config.path())             # a dated copy of config.json at every start, if it changed (it is not in git)
    except OSError:
        pass
    STORE = app_data.Store(DATA_DIR)
    STORE.local = local_cache_dir()
    home_ignore.set_places(lambda: STORE.pl.defs())          # the saved places: where a parked recording is left alone (Home)
    KEPT = app_kept.Kept(local_cache_dir(), _code_version())
    DRIVELIST = app_drivelist.Cached(os.path.join(local_cache_dir(), "drivelist.json"))
    run_cuts.set_file(os.path.join(local_cache_dir(), "run_cuts.json"))
    KINDS = minute_kind.Kinds(os.path.join(local_cache_dir(), "minute_kind_v2.json"))
    home_ignore.set_file(os.path.join(local_cache_dir(), "home_ignore.json"))
    clips_purged.set_file(os.path.join(DATA_DIR, "teslacam_purged.json"))
    SENTRY = app_sentry.Sentry(DATA_DIR)
    TC = app_teslacam.TeslaCam(DATA_DIR, SENTRY)
    TC.tl_days = TLN.DayCache(os.path.join(local_cache_dir(), "timeline_days.json"))      # the Drives timeline by day: a start rebuilds only the days that changed
    threading.Thread(target=footage_roots, daemon=True).start()                   # which drives are connected, found before the first map asks (a slow drive takes seconds)
    threading.Thread(target=lambda: (time.sleep(10) if DRIVELIST.has() else None, drivelist(True)), daemon=True).start()        # ready before the page asks; with a list kept from the last run the window's first page goes first
    DRV = app_drive_review.DriveReview(DATA_DIR, TC)
    FG = app_footage.Footage(DATA_DIR)
    KEEP = app_keeper.Keeper(DATA_DIR, TC, FG, DRV, SENTRY, footage_roots)
    TODO = footage_todo.Todo(os.path.join(local_cache_dir(), "footage_todo.json"), DATA_DIR, TC, FG, footage_roots, lambda: KEEP._stage is None and not KEEP.busy())
    KEEP.on_progress = TODO.subtract
    CKEEP = clip_keep.ClipKeep(DATA_DIR, TC, FG)
    CKEEP.roots_now = footage_roots          # a drive that appears while signs are read: the signs wait (clip_keep._signs_yield)
    run_cuts.on_change(lambda: CKEEP.invalidate())          # what is kept is grouped by the same runs
    CKEEP.on_change = TODO.forget
    CKEEP.roots_now = footage_roots                 # a drive that appears while signs are read: its pictures come first (lib/clip_keep.py _signs_yield)
    TODO.keep = CKEEP                  # the drives' Tasks count what an update would still do: one definition with TeslaCam's button (TODO-068)
    KEEP.page_job = lambda: bool(CKEEP.job().get("running"))
    KEEP.pictures = CKEEP          # the Keeper saves the pictures of events after the stills
    STHIN = stills_thin.StillsThin(DATA_DIR, FG.stills, TC)
    if not a.no_background:
        KEEP.start()
    BOOK = app_charges.ChargeBook(os.path.dirname(os.path.abspath(__file__)), DATA_DIR)
    SYNC = app_sync.ChargerSync(CMDS, DATA_DIR)
    REACH = app_reach.ReachService(os.path.dirname(os.path.abspath(__file__)), DATA_DIR)
    TSYNC = app_sync.ChargerSync(CMDS, DATA_DIR, "sync_drives_all.sh")
    CSYNC = app_sync.ChargerSync(CMDS, DATA_DIR, "sync_charging_all.sh")
    CCOST = app_sync.ChargerSync(CMDS, DATA_DIR, "analyse_charging.py",
                                 argv=[sys.executable, "analyse_charging.py", "--sync-tessie-costs", "--apply", "--only-confident"])
    SOFT = app_software.SoftSync(DATA_DIR)
    CARW = car_watch.Watch(DATA_DIR, lambda: app_software.S.load().get("car_check_minutes") if app_sync.tessie_configured() else 0)
    CARW.start()
    FSYNC = app_sync.ChargerSync(CMDS, DATA_DIR, "sync_fuel_history.py", argv=[sys.executable, "sync_fuel_history.py", "--sync-all"])
    threading.Thread(target=tessie_auto, daemon=True).start()
    threading.Thread(target=fuel_auto, daemon=True).start()
    try:
        srv = serve(a.port)
    except OSError:
        srv = serve(0)      # port taken: any free one
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("Tesla app: %s   (data: %s)" % (url, DATA_DIR), flush=True)
    threading.Thread(target=_warm, args=(srv.server_address[1],), daemon=True).start()
    if a.no_window:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not a.browser:
        try:
            import webview      # optional: native window
            global WIN
            WIN = win = webview.create_window("Tesellate", url, min_size=(900, 600), text_select=True, **window_place(webview))
            win.events.resized += lambda w, h: window_keep(w=w, h=h)
            win.events.moved += lambda x, y: window_keep(x=x, y=y)
            menu = mac_shell.build_menu(win) if sys.platform == "darwin" else None            # the pages in the menu bar
            mac_shell.start(KEEP.status, CKEEP.job)                                            # the job's share on the Dock icon, and a notification when a long job ends
            try:                # the Dock shows the app's icon even though python is the running program (needs pyobjc, which pywebview brings on a Mac)
                import AppKit
                AppKit.NSApplication.sharedApplication().setApplicationIconImage_(AppKit.NSImage.alloc().initWithContentsOfFile_(os.path.join(UI, "icon", "Tesellate_Icon.png")))
            except Exception:
                pass
            webview.start(menu=menu) if menu else webview.start()
            return 0
        except Exception as e:
            # no window and no browser: say so loudly and quit (a silent sleeping process shows as "Application Not Responding")
            import traceback
            msg = "Tesellate could not open its window: %s" % ("pywebview is not installed" if isinstance(e, ImportError) else e)
            print(msg, flush=True)
            traceback.print_exc()
            if sys.platform == "darwin":
                try:
                    subprocess.run(["osascript", "-e", 'display alert "Tesellate" message %s' % json.dumps(msg[:300] + "  (details: ~/Library/Logs/tesla_app.log)")], timeout=60)
                except Exception:
                    pass
            return 1
    # --browser: no window on purpose; never open a browser by ourselves, the URL is printed above
    print("Serving without a window; open %s yourself if you want it in a browser." % url, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
