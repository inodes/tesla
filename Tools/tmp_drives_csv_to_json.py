#!/usr/bin/env python3
"""tmp_drives_csv_to_json.py - TEMPORARY. Moves every drive's telemetry from CSV to Tessie's JSON, checks it, and archives the CSV. Delete this script when
the status says ALL DONE (it is listed as temporary in docs/COMMANDS_AUDIT.md).

NAME
    tmp_drives_csv_to_json.py - fetch, compare, check the code, archive, report: steps 1 to 5 of the CSV -> JSON move, in one resumable run

SYNOPSIS
    tmp_drives_csv_to_json.py [--data-dir DIR] [--status] [--limit N] [--budget SECONDS] [--pace SECONDS] [--since YYYY-MM-DD]
                              [--accept-edges] [--accept-extra] [--retry] [--no-archive] [--restore] [--deep]

DESCRIPTION
    Run it as often as you like: it keeps its place (<data>/drives/json_migration.json), starts by showing where it is, carries on from there and ends by
    showing where it got to. Nothing is ever deleted: a CSV is only MOVED to <data>/drives/archive/deepdive_csv/ (same folders under it), and
    --restore moves them all back.

    Step 1  Fetch    for each drive that has only a CSV, ask Tessie for the same window as JSON (format=json, timezone=UTC, interval=1): one request per drive,
                     paced, with exponential backoff and retry on rate limits / server errors / dropped connections; resumes where it stopped; a drive
                     that already has a good JSON file is not asked again. The JSON file is Tessie's answer, byte for byte, and gets the CSV's file date
                     (so nothing else in the app thinks the drive changed).
    Step 2  Compare  every CSV row is looked up by its UTC second in the JSON and every column compared (numbers to the rounding the CSV had), and the JSON
                     must not hold points inside the CSV's window that the CSV lacks. "identical" is the only result that is archived on its own; a
                     difference only in the first or last row (the edge of the window Tessie asked for) needs --accept-edges, extra JSON points need
                     --accept-extra. Any other discrepancy BACKS THE FETCH OUT: the JSON is moved (never deleted) to <data>/drives/archive/json_rejected/,
                     so the CSV stays the only file the app reads for that drive; the differences are listed and in the log; --retry asks Tessie again.
    Step 3  Code     before any CSV is moved the script checks that the code reads JSON AND CSV (lib/drive_points.py, lib/drive_paths.py, the analyzer, the
                     route reader) on a made-up drive of each kind, that JSON wins when both exist and the CSV is the fallback, and that the comparison
                     itself can tell a wrong file from a right one. If any check fails nothing is archived.
    Step 4  Archive  a confirmed drive's CSV is moved to the archive folder, checked (hash, and the drive still reads from its JSON), and put back at once if not.
    Step 5  Status   a table of every step and the drives still to do or in trouble, shown at the start and at the end of each run (--status shows it only).
                     Archived drives are re-checked each time (their JSON is there and the right size; --deep reads them all again).

OPTIONS
    --data-dir DIR   the Tesla data folder (default $TESLA_DATA_DIR or the iCloud Tesla folder)
    --status         only show the status (no network, nothing changed)
    --limit N        fetch at most N drives in this run
    --budget SECONDS stop starting new work after this many seconds (use ~85 on the device bridge, then run again)
    --pace SECONDS   pause between requests (default 0.4)
    --since DATE     only drives whose folder date is on or after DATE
    --accept-edges   archive drives that match except for the first/last row
    --accept-extra   archive drives whose JSON has more points than the CSV, the CSV's all matching
    --retry          ask again for drives marked failed, empty or different
    --no-archive     do steps 1, 2, 3 and 5 only
    --restore        move every archived CSV back to its place
    --deep           in the status, re-read every archived JSON (slower)

EXIT STATUS
    0 nothing left to do (or nothing wrong so far), 1 work remains or drives need a look, 2 the code check failed, 3 Tessie refused the token.
"""
import argparse
import glob
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))          # the same two roots as tesla_app.py: "lib.x" and "x" both work
import drive_paths as DP  # noqa: E402
import drive_points as P  # noqa: E402
from progress import Bar  # noqa: E402

STATE_NAME = "json_migration.json"
LOG_NAME = "json_migration.log"
ARCHIVE_SUB = os.path.join("drives", "archive", "deepdive_csv")
REJECT_SUB = os.path.join("drives", "archive", "json_rejected")
PAD_RETRIES = 6
DEFAULT_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
TOL_POS, TOL_NUM = 2e-6, 0.0101          # latitude/longitude, everything else (the CSV rounds to two decimals)
SAVE_EVERY = 10


class AuthError(Exception):
    pass


class Log:
    """A verbose log, appended to <data>/drives/json_migration.log (one line per event, timestamped, flushed at once so a killed run still leaves its trail).
    It holds every request made (never the token), what came back, every comparison and every file moved - for finding out why something went wrong."""

    def __init__(self, path=None):
        self.f = None
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.exists(path) and os.path.getsize(path) > 8 << 20:
                    os.replace(path, path + ".1")
                self.f = open(path, "a", encoding="utf-8", buffering=1)
            except OSError:
                self.f = None

    def w(self, msg):
        if self.f:
            try:
                self.f.write("%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], msg))
            except (OSError, ValueError):
                pass

    def close(self):
        if self.f:
            try:
                self.f.close()
            except OSError:
                pass


LOG = Log()


def snip(body, n=400):
    """The start of a response body, as text, for the log."""
    try:
        t = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
    except Exception:
        t = repr(body)
    t = " ".join(t.split())
    return t[:n] + ("... [%d chars in all]" % len(t) if len(t) > n else "")


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- state
class State:
    def __init__(self, data):
        self.path = os.path.join(data, "drives", STATE_NAME)
        self.d = {"version": 1, "drives": {}, "code_check": None, "runs": []}
        try:
            with open(self.path, encoding="utf-8") as fh:
                j = json.load(fh)
            if isinstance(j, dict) and isinstance(j.get("drives"), dict):
                self.d.update(j)
        except (OSError, ValueError):
            pass
        self._dirty, self._last = 0, time.time()

    @property
    def drives(self):
        return self.d["drives"]

    def touch(self):
        self._dirty += 1
        if self._dirty >= SAVE_EVERY or time.time() - self._last > 20:
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.d, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)
        self._dirty, self._last = 0, time.time()


# ---------------------------------------------------------------- the files on disk
def scan(deepdive, ext):
    """{stamp: path} of every drive telemetry file with this extension, all layouts (the day layout wins)."""
    found = {}
    for pat in (os.path.join(deepdive, "%s*%s" % (DP.PREFIX, ext)), os.path.join(deepdive, "*", "%s*%s" % (DP.PREFIX, ext))):          # flat, per-drive
        for p in glob.glob(pat):
            found[DP.stamp_of(p)] = p
    for p in glob.glob(os.path.join(deepdive, "*", "*", "%s*%s" % (DP.PREFIX, ext))):
        found[DP.stamp_of(p)] = p
    found.pop(None, None)
    return found


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def json_ok(path):
    """-> number of points in a Tessie JSON file, 0 when it is missing or not a points answer."""
    try:
        with open(path, "rb") as fh:
            return len(P.parse_json_bytes(fh.read()))
    except OSError:
        return 0


def sync(state, data, since=None):
    """Bring the state in line with the disk (the disk wins): new CSVs become pending, a JSON that is already there and good is 'fetched'. -> the work list."""
    deepdive = os.path.join(data, "drives", "deepdive")
    csvs, jsons = scan(deepdive, ".csv"), scan(deepdive, ".json")
    for stamp, cp in csvs.items():
        if since and stamp[:10] < since:
            continue
        r = state.drives.setdefault(stamp, {"status": "pending", "attempts": 0})
        r["csv"] = os.path.relpath(cp, data)
        if r["status"] == "archived":                       # the CSV is back in the tree (restored by hand): look at it again
            r["status"] = "pending"
        jp = jsons.get(stamp)
        if jp and r["status"] in ("pending", "failed") and json_ok(jp):
            r["status"], r["json"] = "fetched", os.path.relpath(jp, data)
    return csvs


# ---------------------------------------------------------------- step 2: compare
def same_val(head, a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if a == b:
        return True
    if a == "" or b == "":
        return False
    try:
        return abs(float(a) - float(b)) <= (TOL_POS if head in ("Latitude", "Longitude") else TOL_NUM)
    except ValueError:
        return False


def compare(csv_path, json_path):
    """Every CSV row against the JSON point of the same UTC second. -> dict(kind, csv_points, json_points, same, missing, diffs, extra).
    kind: identical | edge_diff (only the first/last row differs) | json_more (all CSV rows match, the JSON holds more inside the window) | differs."""
    c = P.points_epoch(csv_path)
    j = dict(P.points_epoch(json_path))
    if not c:
        return dict(kind="differs", csv_points=0, json_points=len(j), same=0, missing=0, diffs=[], extra=0, why="the CSV has no points")
    heads = [h for h in P.HEADINGS[1:] if h in c[0][1]]
    first, last = c[0][0], c[-1][0]
    cs = {e for e, _ in c}
    missing, diffs, same = [], [], 0
    for e, row in c:
        jr = j.get(e)
        if jr is None:
            missing.append(e)
            continue
        bad = [h for h in heads if not same_val(h, row.get(h, ""), jr.get(h, ""))]
        if bad:
            diffs.append((e, bad[0], row.get(bad[0], ""), jr.get(bad[0], "")))
        else:
            same += 1
    extra = [e for e in j if first <= e <= last and e not in cs]
    out = dict(csv_points=len(c), json_points=len(j), same=same, missing=len(missing), extra=len(extra),
               diffs=[dict(at=int(e), col=h, csv=a, json=b) for e, h, a, b in diffs[:5]])
    bad_at = set(missing) | {e for e, *_ in diffs}
    if not bad_at and not extra:
        out["kind"] = "identical"
    elif not bad_at:
        out["kind"] = "json_more"
    elif bad_at <= {first, last} and not extra:
        out["kind"] = "edge_diff"
    else:
        out["kind"] = "differs"
    return out


# ---------------------------------------------------------------- step 3: the code reads JSON and CSV
def selfcheck():
    """-> list of problems (empty = the code can use JSON and still read CSV, and the comparison can tell right from wrong)."""
    problems = []

    def need(ok, what):
        if not ok:
            problems.append(what)
    try:
        with tempfile.TemporaryDirectory() as d:
            pts = [{"id": 1780267418 + i * 10, "timestamp": 1780267418 + i * 10, "state": "online", "shift_state": "D", "battery_level": 68, "latitude": -33.816 + i * 1e-4,
                    "longitude": 151.0759, "elevation": 14, "speed": 40 + i, "power": None, "odometer": 51.84 + i * 0.1, "locked": 1} for i in range(6)]
            folder = os.path.join(d, "2026-06-01", "2026-06-01_08-46_AEST")
            os.makedirs(folder)
            jp = os.path.join(folder, "drive_deepdive_2026-06-01_08-46_AEST.json")
            cp = os.path.join(folder, "drive_deepdive_2026-06-01_08-46_AEST.csv")
            with open(jp, "w", encoding="utf-8") as fh:
                json.dump({"results": pts}, fh)
            # a CSV written the way Tessie writes it: local text in Sydney time, the zone it is in now in the heading
            lines = ['"' + '","'.join(["Timestamp (AEDT)"] + P.HEADINGS[1:]) + '"']
            for p in pts:
                row = [P.local_text(p["timestamp"], "Australia/Sydney")]
                for h, k in P.FIELDS[1:]:
                    v = p.get(k)
                    row.append("" if v is None else ("%.6f" % v if h in ("Latitude", "Longitude") else ("%.2f" % v if isinstance(v, float) else str(v))))
                lines.append('"' + '","'.join(row) + '"')
            with open(cp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            need(P.epochs(jp) == P.epochs(cp) == [float(p["timestamp"]) for p in pts], "JSON and CSV give different UTC seconds")
            need(P.table(jp)[0][0] == "Timestamp" == P.table(cp)[0][0], "a zone is still part of a heading")
            need(compare(cp, jp)["kind"] == "identical", "the comparison says a matching JSON and CSV differ: %s" % compare(cp, jp))
            bad = os.path.join(d, "variant.json")                 # not named like a drive file, so the listing checks below do not see it
            pb = [dict(p) for p in pts]
            pb[3]["speed"] += 5
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump({"results": pb}, fh)
            need(compare(cp, bad)["kind"] == "differs", "the comparison did not notice a changed value")
            pe = [dict(p) for p in pts]
            pe[0]["latitude"] += 0.001
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump({"results": pe}, fh)
            need(compare(cp, bad)["kind"] == "edge_diff", "the comparison did not see an edge-only difference as one")
            need(DP.list_data(d) == [jp] and DP.find_data(d, "2026-06-01_08-46_AEST") == jp, "with both files the JSON must win in lib/drive_paths.py")
            os.replace(jp, jp + ".hold")
            need(DP.list_data(d) == [cp] and DP.find_data(d, "2026-06-01_08-46_AEST") == cp, "the CSV is no longer the fallback in lib/drive_paths.py")
            os.replace(jp + ".hold", jp)
            import app_route
            need(app_route.points(jp) == app_route.points(cp) != [], "the route reader gives different points for JSON and CSV")
            from lib.drives.analyzer import DriveAnalyzer
            an = DriveAnalyzer.__new__(DriveAnalyzer)
            a, b = an.parse_detailed_drive_csv(jp), an.parse_detailed_drive_csv(cp)
            need(bool(a) and bool(b) and a["start_epoch"] == b["start_epoch"] and a["samples_count"] == b["samples_count"], "the drive analyzer reads JSON and CSV differently")
    except Exception as e:                                     # a broken import is a failed check, not a crash
        problems.append("the check itself failed: %r" % (e,))
    return problems


# ---------------------------------------------------------------- step 1: fetch with backoff
def request_with_backoff(api, vin, token, params, sleep=time.sleep, rng=random.random, say=None):
    """-> (status, body, error). Retries 429 / 5xx / dropped connections with exponential backoff (2, 4, 8 ... up to 90 s, jittered); other 4xx are final;
    401/403 raise AuthError."""
    status, body = None, b""
    for attempt in range(1, PAD_RETRIES + 1):
        t0 = time.time()
        status, body = api("states", vin, token, params)
        LOG.w("REQUEST states %s try %d -> status %s, %d bytes, %.2f s%s" % (
            " ".join("%s=%s" % (k, v) for k, v in sorted(params.items()) if k != "access_token"), attempt, status, len(body or b""), time.time() - t0,
            "" if status == 200 else "; body: " + snip(body)))
        if status == 200:
            return 200, body, None
        if status in (401, 403):
            raise AuthError("HTTP %s" % status)
        retry = status is None or status in (408, 425, 429) or status >= 500
        if not retry:
            return status, body, "HTTP %s" % status
        wait = min(90.0, 2.0 * (2 ** (attempt - 1))) * (0.75 + 0.5 * rng())
        if status == 429:
            wait = max(wait, 15.0)
        LOG.w("  will retry after %.1f s (status %s)" % (wait, status))
        if attempt < PAD_RETRIES:
            if say:
                say("   %s - waiting %.0f s, try %d of %d" % ("no connection" if status is None else "HTTP %s" % status, wait, attempt + 1, PAD_RETRIES))
            sleep(wait)
    return status, body, ("no connection" if status is None else "HTTP %s" % status) + ", gave up after %d tries" % PAD_RETRIES


def fetch_one(rec, data, api, vin, token, sleep, say):
    """Ask Tessie for this drive's window. Writes <folder>/drive_deepdive_<stamp>.json and sets rec['status'] to fetched / empty / failed."""
    cp = os.path.join(data, rec["csv"])
    ep = P.epochs(cp)
    rec["attempts"] = rec.get("attempts", 0) + 1
    LOG.w("FETCH %s: %d CSV points, first %s last %s, attempt %d overall" % (rec["csv"], len(ep), ep[0] if ep else None, ep[-1] if ep else None, rec["attempts"]))
    if not ep:
        rec.update(status="failed", error="the CSV has no readable points")
        return
    from lib.tessie_api_common import STATES_PARAMS
    params = dict(STATES_PARAMS)
    # The CSV's own window, so the two can be compared row for row. Tessie's "to" is EXCLUSIVE (found on the real run: 671 drives came back one point short,
    # the CSV's last row, 30 s after the JSON's last), so ask up to one second past the last row.
    params.update({"from": int(min(ep)), "to": int(max(ep)) + 1, "interval": 1})
    rec["window"] = [params["from"], params["to"]]
    status, body, err = request_with_backoff(api, vin, token, params, sleep=sleep, say=say)
    if err:
        LOG.w("FETCH %s failed: %s" % (rec.get("csv"), err))
        rec.update(status="failed", error=err)
        return
    pts = P.parse_json_bytes(body)
    if not pts:
        LOG.w("FETCH %s EMPTY: the answer holds no points. window %s..%s (%s .. %s UTC); the CSV has %d points. Whole body: %s" % (
            rec.get("csv"), params["from"], params["to"], datetime.utcfromtimestamp(params["from"]), datetime.utcfromtimestamp(params["to"]), len(ep), snip(body, 2000)))
        rec.update(status="empty", error="Tessie answered without any points: " + snip(body, 120))
        return
    jp = os.path.splitext(cp)[0] + ".json"
    tmp = jp + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(body)
    st = os.stat(cp)
    os.utime(tmp, (st.st_atime, st.st_mtime))                                           # the file date of the drive's record does not change
    os.replace(tmp, jp)
    rec.update(status="fetched", json=os.path.relpath(jp, data), json_points=len(pts), json_bytes=os.path.getsize(jp), fetched_at=now_iso(), error="")
    LOG.w("FETCH %s ok: %d points (CSV had %d), %d bytes, window %s..%s, file date set to %s" % (rec.get("csv"), len(pts), len(ep), rec["json_bytes"], params["from"], params["to"], datetime.fromtimestamp(st.st_mtime)))


def reject_json(rec, data, c):
    """Back the fetch out: the JSON that does not match its CSV is moved out of the drive folder (to REJECT_SUB, never deleted) so the CSV stays the only
    file the app reads for that drive. -> True when it was moved."""
    jp = os.path.join(data, rec.get("json", ""))
    why = "%d of %d CSV rows differ, %d missing in the JSON, %d extra in the JSON (%s)" % (
        c["csv_points"] - c["same"], c["csv_points"], c["missing"], c["extra"], c["kind"])
    if os.path.isfile(jp):
        dest = os.path.join(data, REJECT_SUB, os.path.relpath(jp, os.path.join(data, "drives", "deepdive")))
        if os.path.exists(dest):
            dest = "%s.%s" % (dest, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(jp, dest)
        rec["rejected_json"] = os.path.relpath(dest, data)
    rec.update(status="rejected", error=why)
    rec.pop("json", None)
    LOG.w("REJECT %s: fetch backed out, JSON moved to %s; %s; %s" % (rec["csv"], rec.get("rejected_json"), why, json.dumps(c["diffs"][:5])))
    return True


# ---------------------------------------------------------------- step 4: archive
def archive_one(rec, data):
    """Move the CSV to the archive and prove the drive still reads from its JSON. -> True when archived."""
    cp, jp = os.path.join(data, rec["csv"]), os.path.join(data, rec.get("json", ""))
    deepdive = os.path.join(data, "drives", "deepdive")
    if not os.path.isfile(cp) or not json_ok(jp):
        rec["error"] = "the CSV or the JSON is missing"
        return False
    h = sha256(cp)
    dest = os.path.join(data, ARCHIVE_SUB, os.path.relpath(cp, deepdive))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        if sha256(dest) != h:
            rec["error"] = "a different file is already in the archive at %s" % os.path.relpath(dest, data)
            return False
        os.remove(cp)
    else:
        shutil.move(cp, dest)
    stamp = DP.stamp_of(cp)
    ok = os.path.isfile(dest) and sha256(dest) == h and DP.find_data(deepdive, stamp) == jp and len(P.epochs(jp)) > 0
    if not ok:                                                                          # put it back, say so
        if os.path.isfile(dest) and not os.path.exists(cp):
            shutil.move(dest, cp)
        rec["error"] = "the archive check failed; the CSV was put back"
        LOG.w("ARCHIVE %s FAILED the check and was put back" % rec["csv"])
        return False
    rec.update(status="archived", archive=os.path.relpath(dest, data), csv_sha256=h, archived_at=now_iso(), error="")
    LOG.w("ARCHIVE %s -> %s (sha256 %s)" % (rec["csv"], rec["archive"], h[:16]))
    return True


def restore_all(state, data, say=print):
    n = 0
    for stamp, r in sorted(state.drives.items()):
        if r.get("status") != "archived" or not r.get("archive"):
            continue
        src, dst = os.path.join(data, r["archive"]), os.path.join(data, r["csv"])
        if os.path.isfile(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            LOG.w("RESTORE %s -> %s" % (r["archive"], r["csv"]))
            r.update(status="verified", error="")
            n += 1
    state.save()
    say("Moved %d CSV file(s) back into the drive folders." % n)
    return n


# ---------------------------------------------------------------- step 5: status
ORDER = ["pending", "failed", "empty", "fetched", "rejected", "differs", "edge_diff", "json_more", "verified", "archived"]


def recheck_archived(state, data, deep=False):
    """-> list of (stamp, problem) for archived drives whose JSON or archived CSV is not as recorded."""
    bad = []
    for stamp, r in state.drives.items():
        if r.get("status") != "archived":
            continue
        jp, ap = os.path.join(data, r.get("json", "")), os.path.join(data, r.get("archive", ""))
        if not os.path.isfile(jp):
            bad.append((stamp, "its JSON is missing"))
        elif r.get("json_bytes") and os.path.getsize(jp) != r["json_bytes"]:
            bad.append((stamp, "its JSON changed size"))
        elif deep and json_ok(jp) != r.get("json_points"):
            bad.append((stamp, "its JSON has a different number of points"))
        elif not os.path.isfile(ap):
            bad.append((stamp, "its archived CSV is missing"))
    return bad


def tally(state):
    t = {k: 0 for k in ORDER}
    for r in state.drives.values():
        t[r.get("status", "pending")] = t.get(r.get("status", "pending"), 0) + 1
    return t


def status_text(state, data, args):
    t = tally(state)
    total = sum(t.values())
    n = lambda x: "{:,}".format(x)
    have_json = t["fetched"] + t["differs"] + t["edge_diff"] + t["json_more"] + t["verified"] + t["archived"]
    compared = t["rejected"] + t["differs"] + t["edge_diff"] + t["json_more"] + t["verified"] + t["archived"]
    cc = state.d.get("code_check")
    bad = recheck_archived(state, data, deep=args.deep)
    L = ["", "CSV -> JSON for drive telemetry   (%s)" % os.path.join(data, "drives", STATE_NAME), "-" * 78,
         "Drives with a telemetry file: %s" % n(total),
         "Step 1  Fetch JSON       %s of %s have their JSON    waiting %s   failed %s   Tessie has none %s" % (n(have_json), n(total), n(t["pending"]), n(t["failed"]), n(t["empty"])),
         "Step 2  Compare with CSV %s compared: identical %s   first/last row only %s   JSON has more %s   differ %s   (not yet compared %s)" % (
             n(compared), n(t["verified"] + t["archived"]), n(t["edge_diff"]), n(t["json_more"]), n(t["differs"] + t["rejected"]), n(t["fetched"])),
         "                         wrong JSON backed out (moved to %s, the CSV stays in use): %s" % (REJECT_SUB, n(t["rejected"])),
         "Step 3  Code reads both  " + ("not checked yet" if not cc else ("OK (checked %s)" % cc["at"] if cc["ok"] else "FAILED (%s)" % "; ".join(cc["problems"]))),
         "Step 4  CSV archived     %s of %s   (in %s)" % (n(t["archived"]), n(total), ARCHIVE_SUB),
         "Step 5  Re-check         archived drives: %s read back fine, %s with a problem" % (n(t["archived"] - len(bad)), n(len(bad)))]
    troubles = [(s, r) for s, r in sorted(state.drives.items()) if r.get("status") in ("failed", "empty", "rejected", "differs", "edge_diff", "json_more")]
    if troubles:
        L.append("")
        L.append("Drives that need a look (first 12 of %d):" % len(troubles))
        for s, r in troubles[:12]:
            d = r.get("diffs") or []
            why = r.get("error") or ""
            if r["status"] in ("rejected", "differs", "edge_diff", "json_more"):
                why = "%d of %d rows differ, %d missing in JSON, %d extra in JSON" % (r.get("csv_points", 0) - r.get("same", 0), r.get("csv_points", 0), r.get("missing", 0), r.get("extra", 0))
                if d:
                    why += "; e.g. %s: CSV %s, JSON %s" % (d[0]["col"], d[0]["csv"], d[0]["json"])
            L.append("  %-24s %-10s %s" % (s, r["status"], why))
    if bad:
        L.append("")
        L.append("Archived drives to check (first 8 of %d):" % len(bad))
        for s, why in bad[:8]:
            L.append("  %-24s %s" % (s, why))
    todo = t["pending"] + t["fetched"] + t["verified"] + t["failed"]
    L.append("")
    if total == 0:
        L.append("Nothing to do: no drive has a CSV telemetry file.")
    elif t["archived"] == total and not bad:
        L.append("ALL DONE: every drive reads from its JSON, every CSV is in the archive. This script can be deleted.")
    elif todo:
        L.append("Run it again to carry on (%s drive(s) still to go)." % n(todo))
    else:
        L.append("Everything that can be done on its own is done; the drives listed above need a look (--retry asks Tessie again; --accept-edges / --accept-extra keep a JSON that differs only at the edge / has extra points).")
    return "\n".join(L), bool(bad), todo + len(troubles)


# ---------------------------------------------------------------- the run
def run(args, api=None, creds=None, sleep=time.sleep, out=print):
    data = os.path.abspath(os.path.expanduser(args.data_dir))
    deepdive = os.path.join(data, "drives", "deepdive")
    if not os.path.isdir(deepdive):
        out("No drives/deepdive folder in %s" % data)
        return 1
    global LOG
    LOG.close()
    LOG = Log(os.path.join(data, "drives", LOG_NAME))
    LOG.w("=" * 100)
    LOG.w("RUN started: args %s; data %s; python %s; script %s" % (vars(args), data, sys.version.split()[0], os.path.abspath(__file__)))
    state = State(data)
    t_start = time.time()
    over = lambda: bool(args.budget) and time.time() - t_start > args.budget

    if args.restore:
        restore_all(state, data, out)
        return 0

    # step 3 first: nothing below is worth doing if the code cannot read what it is about to produce
    problems = selfcheck()
    state.d["code_check"] = {"at": now_iso(), "ok": not problems, "problems": problems}
    LOG.w("CODE CHECK: %s" % ("ok" if not problems else "FAILED: " + "; ".join(problems)))
    sync(state, data, args.since)
    LOG.w("STATE at start: %s" % json.dumps(tally(state)))
    state.save()
    text, bad, todo = status_text(state, data, args)
    out(text)
    if problems:
        out("\nSTOP: the code check failed, nothing was fetched or moved:\n  " + "\n  ".join(problems))
        return 2
    if args.status:
        return 1 if (todo or bad) else 0

    csvs = sync(state, data, args.since)
    code = 0
    try:
        # ---- step 1: fetch
        if args.retry:                                          # failed / empty: ask again; different: compare again (the JSON may have been replaced)
            for r in state.drives.values():
                if r.get("status") in ("failed", "empty", "rejected"):
                    r["status"] = "pending"
                elif r.get("status") in ("differs", "edge_diff", "json_more"):
                    r["status"] = "fetched" if r.get("json") and json_ok(os.path.join(data, r["json"])) else "pending"
        todo1 = [s for s in sorted(csvs, reverse=True) if state.drives.get(s, {}).get("status") in ("pending", "failed") and (not args.since or s[:10] >= args.since)]
        if args.limit:
            todo1 = todo1[:args.limit]
        if todo1:
            if creds is None:
                from lib.tessie_api_common import load_config, get_token, get_vin
                cfg = load_config()
                creds = lambda: (get_token(None, cfg), get_vin(None, cfg))
            token, vin = creds()
            if not token or not vin:
                out("\nStep 1 skipped: no Tessie token / VIN is configured (config.json). Steps 2 to 5 carry on with what is already fetched.")
            else:
                if api is None:
                    from lib.tessie_api_common import api_get as api
                out("\nStep 1  Fetching %d drive(s) from Tessie (about one request each; failures are retried with a growing pause)" % len(todo1))
                bar, fails = Bar(len(todo1), "Fetch JSON"), 0
                for i, s in enumerate(todo1, 1):
                    if over():
                        bar.note("Time budget reached - run again to carry on.")
                        break
                    bar.update(i - 1, s)
                    r = state.drives[s]
                    fetch_one(r, data, api, vin, token, sleep, bar.note)
                    state.touch()
                    if r["status"] == "failed":
                        fails += 1
                        bar.note("  %s: %s" % (s, r.get("error")))
                        if fails >= 5 and all(state.drives[x].get("status") == "failed" for x in todo1[max(0, i - 5):i]):
                            bar.note("Five drives in a row failed: stopping. Run it again later; it carries on from here.")
                            break
                    else:
                        fails = 0
                    if args.pace:
                        sleep(args.pace)
                bar.update(min(i, len(todo1)) if todo1 else 0, "")
                bar.done()
        # ---- step 2: compare
        accepted = ({"edge_diff"} if args.accept_edges else set()) | ({"json_more"} if args.accept_extra else set())
        todo2 = [s for s, r in sorted(state.drives.items()) if s in csvs and (r.get("status") == "fetched" or (
            r.get("status") in ("differs", "edge_diff", "json_more") and r.get("json") and r["status"] not in accepted
            and os.path.isfile(os.path.join(data, r["json"]))))]
        if todo2:
            out("\nStep 2  Comparing %d drive(s) with their CSV" % len(todo2))
            bar = Bar(len(todo2), "Compare")
            for i, s in enumerate(todo2, 1):
                if over():
                    bar.note("Time budget reached - run again to carry on.")
                    break
                bar.update(i - 1, s)
                r = state.drives[s]
                try:
                    c = compare(os.path.join(data, r["csv"]), os.path.join(data, r["json"]))
                except OSError as e:
                    r.update(status="failed", error="could not read a file: %s" % e)
                    continue
                r.update({k: c[k] for k in ("csv_points", "json_points", "same", "missing", "extra", "diffs")})
                LOG.w("COMPARE %s: %s (csv %d, json %d, same %d, missing %d, extra %d) %s" % (s, c["kind"], c["csv_points"], c["json_points"], c["same"], c["missing"], c["extra"], json.dumps(c["diffs"])))
                r.update(status={"identical": "verified"}.get(c["kind"], c["kind"]), verified_at=now_iso())
                if c["kind"] != "identical" and c["kind"] not in accepted:
                    reject_json(r, data, c)                      # a discrepancy: the fetch is backed out, the CSV stays the drive's data
                state.touch()
            bar.update(len(todo2), "")
            bar.done()
        # ---- step 4: archive
        if not args.no_archive:
            ok_kinds = {"verified"} | ({"edge_diff"} if args.accept_edges else set()) | ({"json_more"} if args.accept_extra else set())
            todo4 = [s for s, r in sorted(state.drives.items()) if r.get("status") in ok_kinds and s in csvs]
            if todo4:
                out("\nStep 4  Archiving %d confirmed CSV file(s) (moved, never deleted)" % len(todo4))
                bar, moved = Bar(len(todo4), "Archive"), 0
                for i, s in enumerate(todo4, 1):
                    if over():
                        bar.note("Time budget reached - run again to carry on.")
                        break
                    bar.update(i - 1, s)
                    if archive_one(state.drives[s], data):
                        moved += 1
                    else:
                        bar.note("  %s: %s" % (s, state.drives[s].get("error")))
                    state.touch()
                bar.update(len(todo4), "")
                bar.done("Archived %d." % moved)
    except AuthError as e:
        LOG.w("AUTH REFUSED: %s" % e)
        out("\nTessie refused the token (%s). Nothing was changed for that drive; fix the token and run again." % e)
        code = 3
    except KeyboardInterrupt:
        LOG.w("INTERRUPTED by the user")
        out("\nStopped. Run again to carry on from here.")
        code = 1
    except Exception:
        import traceback
        LOG.w("CRASH:\n" + traceback.format_exc())
        out("\nSomething went wrong (the details are in %s). Run again to carry on." % os.path.join(data, "drives", LOG_NAME))
        code = 1
    finally:
        state.d["runs"] = (state.d.get("runs") or [])[-19:] + [{"at": now_iso(), "seconds": round(time.time() - t_start)}]
        state.save()
        LOG.w("RUN ended after %.0f s; state: %s" % (time.time() - t_start, json.dumps(tally(state))))
    # ---- step 5: where it got to
    text, bad, todo = status_text(state, data, args)
    out(text)
    out("Log: %s" % os.path.join(data, "drives", LOG_NAME))
    return code or (1 if (todo or bad) else 0)


def parse(argv=None):
    ap = argparse.ArgumentParser(description="TEMPORARY: drive telemetry CSV -> JSON, steps 1-5 in one resumable run.", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--pace", type=float, default=0.4)
    ap.add_argument("--since")
    ap.add_argument("--accept-edges", action="store_true")
    ap.add_argument("--accept-extra", action="store_true")
    ap.add_argument("--retry", action="store_true")
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--deep", action="store_true")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse()))
