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
    opens in your default browser. Either way it is the same local page.

OPTIONS
    --data-dir DIR   Tesla data folder (default: TESLA_DATA_DIR or the iCloud "Tesla" folder)
    --port N         port to listen on (default 8765, or app.window.port in config.json; 0 = any free port)
    --no-window      only run the server (open http://127.0.0.1:PORT yourself)
    --browser        use the default browser even if pywebview is installed

EXAMPLES
    Put each command on its own line. Do not paste a trailing "# comment" after a command: an interactive zsh does not
    treat "#" as a comment, so the comment arrives as extra arguments (this script now ignores them with a warning).

        cd path/to/tesla/Tools
        ./tesla_app.py
        ./tesla_app.py --browser
        ./tesla_app.py --no-window --port 8800
        pip install -r ../requirements.txt
        ./build_mac_app.sh

    ./tesla_app.py opens a native window if pywebview is installed, otherwise your browser. pywebview is in requirements.txt, so direnv
    installs it into the repo venv on its own (never install it globally). ./build_mac_app.sh makes ~/Applications/Tesla.app to
    double-click (log: ~/Library/Logs/tesla_app.log). Stop the app by closing its window or Ctrl-C in the terminal.

FILES
    lib/app_data.py  the data layer (read-only)   lib/app_ui/  the page (index.html, app.css, app.js)
    lib/app_places.py  places: saved places, Tesla names, nearby OpenStreetMap places. The only thing the app writes is
                     places.json, when you press Save / Delete on a place (a dated backup is kept first)
    drive_actions.py runs the footage queue       build_mac_app.sh  makes a double-clickable Tesla.app
"""
import argparse
import io
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import app_config  # noqa: E402
import app_data  # noqa: E402
import app_sentry  # noqa: E402
import app_teslacam  # noqa: E402
import app_charges  # noqa: E402
import app_sync  # noqa: E402
import drive_roles as DR  # noqa: E402

UI = os.path.join(HERE, "lib", "app_ui")
DEFAULT_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
mimetypes.add_type("text/javascript", ".js")
STORE = None
SENTRY = None
TC = None
SYNC = None
BOOK = None
STARTED = time.time()
DATA_DIR = DEFAULT_DATA
FOOT_EXT = (".mp4", ".jpg", ".jpeg", ".png", ".json")
_ACTION_LOCK = threading.Lock()
_ROOTS = {"t": 0, "v": {}}


def footage_root(role):
    """TeslaCam folder of the drive with this role (A Archive, J JOWUA, U USB); looked up at most every 20 s."""
    if time.time() - _ROOTS["t"] > 20:
        _ROOTS["v"] = {r: DR.find_root(r) for r in "AJU"}
        _ROOTS["t"] = time.time()
    return _ROOTS["v"].get(role)


def code_updated():
    """True when a program file changed after this server started (so a restart is needed to use it)."""
    newest = 0
    for d, pat in ((HERE, ".py"), (os.path.join(HERE, "lib"), ".py")):
        try:
            for f in os.listdir(d):
                if f.endswith(pat) and (f.startswith("app_") or f in ("tesla_app.py", "drive_actions.py", "places_manager.py")):
                    newest = max(newest, os.path.getmtime(os.path.join(d, f)))
        except OSError:
            pass
    return newest > STARTED + 2


def drive_page(p):
    """A per-drive page as the generator wrote it; pages built before the map got its reset-view button get it here (no rebuild needed)."""
    with open(p, encoding="utf-8") as fh:
        h = fh.read()
    old = 'map.addControl(new maplibregl.NavigationControl({showCompass:false}),"top-right");'
    if "Show the whole route" in h or old not in h:
        return h
    return h.replace(old, 'const nav=new maplibregl.NavigationControl({showCompass:false});map.addControl(nav,"top-right");\n {const b=document.createElement("button");b.type="button";b.className="maplibregl-ctrl-icon";b.title="Show the whole route";b.setAttribute("aria-label","Show the whole route");b.innerHTML=\'<span style="display:block;text-align:center;font-size:18px;line-height:29px">\\u2922</span>\';\n  b.onclick=()=>map.fitBounds([[Math.min(...O),Math.min(...L)],[Math.max(...O),Math.max(...L)]],{padding:40,maxZoom:16});nav._container.appendChild(b)}')


def _local_ts(txt):
    import datetime
    return datetime.datetime.fromisoformat(txt).timestamp()


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

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        try:
            if path in ("/", "/index.html"):
                return self.send_file(os.path.join(UI, "index.html"))
            if path in ("/app.js", "/app.css"):
                return self.send_file(os.path.join(UI, path[1:]))
            if path.startswith("/api/"):
                return self.api(path[5:], urllib.parse.parse_qs(u.query))
            if path.startswith("/data/"):
                p = safe_join(DATA_DIR, path[6:])
                if p and os.path.isfile(p) and p.endswith(os.path.join("dashcam_flip_review", "index.html")):
                    return self.send_bytes(flip_page(p).encode("utf-8"), "text/html; charset=utf-8")
                if p and os.path.isfile(p) and p.endswith("drive.html") and os.sep + "deepdive" + os.sep in p:
                    return self.send_bytes(drive_page(p).encode("utf-8"), "text/html; charset=utf-8")
                if p and os.path.isfile(p):
                    return self.send_file(p)
                return self.send_bytes(b"not found", "text/plain", 404)
            if path.startswith("/review/"):         # a small picture of a sign that was read
                p = safe_join(os.path.join(DATA_DIR, "drive_review", "crops"), path[8:])
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
            if u.path == "/api/places/save":
                with _ACTION_LOCK:
                    return self.send_json(STORE.pl.save(body))
            if u.path == "/api/places/delete":
                with _ACTION_LOCK:
                    return self.send_json(STORE.pl.delete(body.get("name", "")))
            if u.path == "/api/review":
                role = body.get("role", "A")
                return self.send_json(TC.review(role, footage_root(role), _local_ts(body.get("t0", "")), _local_ts(body.get("t1", "")), body.get("action", "start")))
            if u.path == "/api/teslacam/index":
                role = body.get("role", "A")
                return self.send_json(TC.index(role, footage_root(role), body.get("action", "start")))
            if u.path == "/api/chargers/sync":
                return self.send_json(SYNC.start())
            if u.path == "/api/chargers/sync/stop":
                return self.send_json(SYNC.stop())
            if u.path == "/api/queue/run":
                return self.send_json(run_queue(body))
            return self.send_bytes(b"not found", "text/plain", 404)
        except Exception as e:
            self.send_json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)

    def api(self, name, q):
        S = STORE
        if name == "summary":
            return self.send_json(S.summary())
        if name == "activity":
            return self.send_json(S.activity())
        if name == "battery":
            return self.send_json(dict(readings=S.battery(), spec=S.spec()))
        if name == "analytics":
            return self.send_json(S.analytics())
        if name == "status":
            return self.send_json(dict(roots={"A": bool(footage_root("A"))}, data_dir=DATA_DIR, stale=code_updated()))
        if name == "sentry":
            ev, ok = SENTRY.events(footage_root("A"))
            return self.send_json(dict(connected=ok, events=ev))
        if name == "review":                    # review?role=A&t0=<local ISO>&t1=<local ISO>: signs read so far + the job
            role = q.get("role", ["A"])[0]
            try:
                return self.send_json(TC.review(role, footage_root(role), _local_ts(q.get("t0", [""])[0]), _local_ts(q.get("t1", [""])[0])))
            except ValueError:
                return self.send_json({"error": "bad time"}, 400)
        if name == "review/scanned":            # every minute whose signs have been read: {stamp: signs found}
            return self.send_json(TC.review_scanned())
        if name == "teslacam":                  # the drives that are connected right now (found by what is on them)
            _ROOTS["t"] = 0
            return self.send_json(dict(drives=TC.drives()))
        if name.startswith("teslacam/"):        # teslacam/<role>/<sentry|saved|recent>[/<run>]
            role, _, rest = name[9:].partition("/")
            kind, _, rid = rest.partition("/")
            root = footage_root(role)
            if kind == "timeline":
                return self.send_json(TC.timeline(role, root))
            if kind == "index":
                return self.send_json(TC.index(role, root, "status"))
            if kind == "seg":
                d = TC.segment(role, root, urllib.parse.unquote(rid), float(q.get("budget", ["20"])[0] or 20))
                return self.send_json(d if d else {"error": "no such drive"}, 200 if d else 404)
            if kind == "window":                    # teslacam/<role>/window?t0=<local ISO>&t1=<local ISO>[&budget=s]
                import datetime
                try:
                    t0 = datetime.datetime.fromisoformat(q.get("t0", [""])[0]).timestamp()
                    t1 = datetime.datetime.fromisoformat(q.get("t1", [""])[0]).timestamp()
                except ValueError:
                    return self.send_json({"error": "bad time"}, 400)
                d = TC.window(role, root, t0, t1, float(q.get("budget", ["20"])[0] or 20))
                return self.send_json(d if d else {"error": "no footage or telemetry for this time"}, 200 if d else 404)
            if kind == "recent" and rid:
                c = TC.run_clips(role, root, urllib.parse.unquote(rid))
                return self.send_json(dict(clips=c) if c else {"error": "no such run"}, 200 if c else 404)
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
        if name == "alerts":
            return self.send_json(S.alerts())
        if name == "places":
            return self.send_json(S.pl.listing(S))
        if name == "chargers":
            return self.send_json(S.pl.charger_listing(S))
        if name == "chargers-sync":
            return self.send_json(SYNC.status(int(q.get("since", ["0"])[0] or 0)))
        if name == "settings":
            return self.send_json(app_config.settings())
        if name == "place":
            d = S.pl.detail(S, q.get("key", [""])[0], q.get("name", [""])[0], q.get("addr", [""])[0])
            return self.send_json(d if d else {"error": "no such place"}, 200 if d else 404)
        if name == "place-osm":
            g = lambda k, dv="": q.get(k, [dv])[0]
            try:
                return self.send_json(S.pl.osm(g("key"), float(g("lat")), float(g("lon")), int(float(g("r", "300"))), g("refresh") == "1"))
            except ValueError:
                return self.send_json({"error": "bad coordinates"}, 400)
        if name == "flip-events":
            return self.send_json({"exists": os.path.isfile(os.path.join(DATA_DIR, "drives", "analysis", "dashcam_flip_review", "index.html"))})
        return self.send_bytes(b"not found", "text/plain", 404)


FLIP_SHOW = "const E=DATA.e,SM=DATA.s;"
FLIP_TWEAK = ("const QF=new URLSearchParams(location.search),T0=QF.get('t0'),T1=QF.get('t1');"
              "const E=(T0&&T1?DATA.e.filter(e=>e.flip_time>=T0&&e.flip_time<=T1):DATA.e),SM=DATA.s;"
              "if(QF.get('embed'))document.head.insertAdjacentHTML('beforeend','<style>h1,.how{display:none}.wrap{padding-top:4px}</style>');")


def flip_page(path):
    """The flip review page as the generator wrote it, plus two query options so one drive can show just its own flips inside the app:
    ?t0=YYYY-MM-DD HH:MM:SS&t1=... keeps the flips in that time range, &embed=1 drops the page heading. The file on disk is not changed."""
    with open(path, encoding="utf-8") as fh:
        h = fh.read()
    return h.replace(FLIP_SHOW, FLIP_TWEAK, 1)


def run_queue(body):
    """Run the footage actions queued on the drive pages (drive_actions.py). apply=False only describes what would happen."""
    import drive_actions as DA
    actions = body.get("actions") or []
    apply = bool(body.get("apply"))
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


def serve(port, host="127.0.0.1"):
    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    return srv


def main():
    global STORE, DATA_DIR, SENTRY, TC, SYNC, BOOK
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--port", type=int, default=int(app_config.get("window.port")))
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--browser", action="store_true")
    a, extra = ap.parse_known_args()
    if extra:
        print("(ignoring extra arguments: %s - put comments on their own line, not after the command)" % " ".join(extra))
    DATA_DIR = os.path.expanduser(a.data_dir)
    if not os.path.isdir(DATA_DIR):
        print("Data folder not found: %s (use --data-dir)" % DATA_DIR)
        return 1
    STORE = app_data.Store(DATA_DIR)
    SENTRY = app_sentry.Sentry(DATA_DIR)
    TC = app_teslacam.TeslaCam(DATA_DIR, SENTRY)
    BOOK = app_charges.ChargeBook(os.path.dirname(os.path.abspath(__file__)), DATA_DIR)
    BOOK.get()          # start reconciling the charges with their invoices in the background
    SYNC = app_sync.ChargerSync(os.path.dirname(os.path.abspath(__file__)), DATA_DIR)
    try:
        srv = serve(a.port)
    except OSError:
        srv = serve(0)      # port taken: any free one
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("Tesla app: %s   (data: %s)" % (url, DATA_DIR), flush=True)
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
            webview.create_window("Tesla", url, width=int(app_config.get("window.width")), height=int(app_config.get("window.height")), min_size=(900, 600), text_select=True)
            webview.start()
            return 0
        except ImportError:
            print("(pywebview not installed: opening your browser instead. For a native window: pip install pywebview)")
        except Exception as e:
            print("(window failed: %s; opening your browser)" % e)
    import webbrowser
    webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
