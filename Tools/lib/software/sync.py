"""sync.py - fetch what the software tracker is missing, then report. The command line of lib/software.

NAME
    sync.py - keep the local copy of Teslascope's software versions up to date

SYNOPSIS
    python3 Tools/lib/software/sync.py [--data-dir DIR] [--status] [--limit N] [--budget SECONDS] [--pace SECONDS]
                                       [--since YEAR] [--version V] [--refetch HOURS] [--force]

DESCRIPTION
    Asks Teslascope for its list of versions, then for each version this folder does not hold yet (newest first), and again for the ones still
    moving (cars waiting, or taken recently) after --refetch hours. A version that has settled is never asked again. Run it as often as you like;
    it keeps its place, shows where it is at the start and the end, and stops cleanly on Ctrl-C. Files are Teslascope's answers unchanged, in
    <data>/software/teslascope/; the numbers over time are kept in <data>/software/uptake.jsonl.

OPTIONS
    --data-dir DIR   the Tesla data folder (default $TESLA_DATA_DIR or the iCloud Tesla folder)
    --status         only show where it is (no network)
    --limit N        fetch at most N versions in this run
    --budget SECONDS stop starting new requests after this many seconds
    --pace SECONDS   pause between requests (default from config.json "software", 1.0)
    --since YEAR     only versions of this year or later (the first run for the car's own years is quick; leave it out for everything back to 2018)
    --version V      fetch this one version now, whatever its state
    --refetch HOURS  ask again about a moving version after this many hours (default from config.json, 6)
    --force          ask about every version again

EXIT STATUS
    0 up to date, 1 some versions failed or are still to fetch, 2 Teslascope refused the requests, 3 could not read the list
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
for p in (TOOLS, os.path.join(TOOLS, "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

from lib.fetch_ledger import FetchLedger  # noqa: E402
from lib.progress import Bar  # noqa: E402
from lib.software import settings as S  # noqa: E402
from lib.software import teslascope as T  # noqa: E402
from lib.software import versions as V  # noqa: E402

DEFAULT_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))


def _held(data_dir):
    d = T.paths(data_dir)[1]
    try:
        return {f[:-5] for f in os.listdir(d) if f.endswith(".json")}
    except OSError:
        return set()


def read_list(data_dir):
    try:
        with open(T.paths(data_dir)[2], "rb") as fh:
            r = T.parse_envelope(fh.read())
        return [v for v in r if isinstance(v, str)] if isinstance(r, list) else []
    except OSError:
        return []


def fetch_list(data_dir, get, sleep, log):
    status, body = T.request(T.BASE, get, sleep, log=log)
    r = T.parse_envelope(body) if status == 200 else None
    if not isinstance(r, list) or not r:
        return None
    T._atomic(T.paths(data_dir)[2], body)
    return [v for v in r if isinstance(v, str)]


def plan(data_dir, listed, ledger, now, refetch_hours, since=None, force=False, only=None):
    """-> [(version, why)] newest first: never fetched, or still moving and asked about long enough ago."""
    held = _held(data_dir)
    out = []
    for v in sorted(listed, key=V.key, reverse=True):
        if only and v != only:
            continue
        if since and not (V.parse(v) and V.parse(v)[0] >= since):
            continue
        rec = ledger.get(T.KIND, v)
        if only or force:
            out.append((v, "asked for"))
        elif rec and rec.get("status") == "empty":
            continue
        elif v not in held:
            out.append((v, "new"))
        elif rec and rec.get("status") == "complete":
            continue
        elif rec and rec.get("status") == "empty":
            continue
        elif not rec or now - rec.get("at", 0) >= refetch_hours * 3600:
            out.append((v, "still moving"))
    return out


def fetch_one(data_dir, v, get, sleep, ledger, last, now, settle_days, log):
    """Ask about one version and keep the answer. -> 'complete' | 'live' | 'empty' | 'failed'."""
    status, body = T.request("%s/%s" % (T.BASE, v), get, sleep, log=log)
    if status == 404:
        ledger.record(T.KIND, v, "empty", note="404", source="Teslascope")
        return "empty"
    resp = T.parse_envelope(body) if status == 200 else None
    if not isinstance(resp, dict) or resp.get("version") != v:
        ledger.record(T.KIND, v, "failed", note="HTTP %s, not a version answer" % status, source="Teslascope")
        return "failed"
    path = T.version_file(data_dir, v)
    try:
        with open(path, "rb") as fh:
            same = fh.read() == body
    except OSError:
        same = False
    if not same:
        T._atomic(path, body)
    line = T.uptake_line(resp, now)
    if v not in last or not T._same_numbers(last[v], line):
        T.append_uptake(data_dir, line)
        last[v] = line
    state = "complete" if T.is_settled(resp, now, settle_days) else "live"
    ledger.record(T.KIND, v, state, note="%s cars" % resp.get("count"), source="Teslascope")
    return state


def status_text(data_dir, ledger, now):
    listed = read_list(data_dir)
    held = _held(data_dir)
    recs = {v: ledger.get(T.KIND, v) for v in held}
    complete = sum(1 for r in recs.values() if r and r.get("status") == "complete")
    live = sum(1 for r in recs.values() if r and r.get("status") == "live")
    todo = [v for v in listed if v not in held]
    L = ["Software versions   (%s)" % os.path.join(data_dir, T.DIRNAME), "-" * 70,
         "Teslascope lists   %s versions" % "{:,}".format(len(listed)),
         "Held here          %s   (settled %s, still moving %s)" % ("{:,}".format(len(held)), "{:,}".format(complete), "{:,}".format(live)),
         "Not fetched yet    %s" % "{:,}".format(len(todo))]
    if todo:
        L.append("Newest not fetched  %s" % sorted(todo, key=V.key)[-1])
    return "\n".join(L), len(todo)


def sync(data_dir, get=T.http_get, sleep=time.sleep, now=None, pace=None, limit=0, budget=0, since=None, only=None, refetch_hours=None, force=False,
         settings=None, say=print, bar=True, progress=None, stop=None):
    """The whole run. -> dict(listed, asked, complete, live, empty, failed, left, refused).
    progress(done, total, what) is called before each version (the app draws its bar from it); stop() returning true ends the run between two versions."""
    cfg = settings or S.load()
    now = now if now is not None else time.time()
    pace = cfg["pace_seconds"] if pace is None else pace
    refetch_hours = cfg["refetch_hours"] if refetch_hours is None else refetch_hours
    ledger = FetchLedger(data_dir)
    out = dict(listed=0, asked=0, complete=0, live=0, empty=0, failed=0, left=0, refused=False, list_ok=True)
    t0 = time.time()
    log = lambda s: say(s)
    try:
        listed = fetch_list(data_dir, get, sleep, log)
        if listed is None:
            listed = read_list(data_dir)
            out["list_ok"] = False
            say("Could not read Teslascope's list of versions%s." % (" - using the one held from last time" if listed else ""))
            if not listed:
                return out
        out["listed"] = len(listed)
        everything = plan(data_dir, listed, ledger, now, refetch_hours, since, force, only)
        todo = everything[:limit] if limit else everything
        last = T.last_uptake(data_dir)
        b = Bar(len(todo), "Software versions") if bar and todo else None
        if progress:
            progress(0, len(todo), "")
        for i, (v, why) in enumerate(todo):
            if budget and time.time() - t0 > budget:
                if b:
                    b.note("Time budget reached - run again to carry on.")
                break
            if stop and stop():
                say("Stopped.")
                break
            if progress:
                progress(i, len(todo), v)
            if b:
                b.update(i, v)
            try:
                st = fetch_one(data_dir, v, get, sleep, ledger, last, now, cfg["settle_days"], log)
            except T.Refused:
                raise
            except Exception as e:                       # one odd version must not stop the other hundreds
                ledger.record(T.KIND, v, "failed", note="%s: %s" % (type(e).__name__, e), source="Teslascope")
                st = "failed"
            out["asked"] += 1
            out[st] += 1
            if st == "failed" and b:
                b.note("  %s: could not be fetched" % v)
            if pace and i < len(todo) - 1:
                sleep(pace)
        if progress:
            progress(out["asked"], len(todo), "")
        if b:
            b.update(out["asked"], "")
            b.done("Asked about %d version(s)." % out["asked"])
        out["left"] = max(len(everything) - out["asked"], 0)
    except T.Refused as e:
        out["refused"] = True
        say("Teslascope refused the request (%s). Nothing was changed; try again later." % e)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--pace", type=float, default=None)
    ap.add_argument("--since", type=int, default=None)
    ap.add_argument("--version")
    ap.add_argument("--refetch", type=float, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.help:
        print("NAME" + __doc__.split("NAME", 1)[1].rstrip())
        return 0
    data = os.path.expanduser(a.data_dir)
    ledger = FetchLedger(data)
    text, todo = status_text(data, ledger, time.time())
    print(text)
    if a.status:
        return 1 if todo else 0
    r = sync(data, pace=a.pace, limit=a.limit, budget=a.budget, since=a.since, only=a.version, refetch_hours=a.refetch, force=a.force)
    print()
    text, todo = status_text(data, ledger, time.time())
    print(text)
    if r["refused"]:
        return 2
    if not r["list_ok"] and not r["listed"]:
        return 3
    return 1 if (r["failed"] or todo) else 0


if __name__ == "__main__":
    sys.exit(main())
