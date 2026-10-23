#!/usr/bin/env python3
"""
reference_timeline.py - save, then later check, what the TeslaCam Timeline shows today, so a rebuild of it can prove it lost nothing.

NAME
    reference_timeline.py - reference results for the TeslaCam timeline

SYNOPSIS
    reference_timeline.py --save [--data-dir DIR]
    reference_timeline.py --check [--new] [--data-dir DIR]

DESCRIPTION
    Starts the app on a spare port (read-only: nothing is changed by looking), asks it for the timeline of every connected TeslaCam drive,
    and keeps one short fingerprint per drive and day in <data>/_reference/timeline.json: how many clip minutes and Sentry events the day
    has, and (for days where every minute's telemetry is already read, so the answer cannot change as reading goes on) a fingerprint of the
    list of drives, stops and Sentry events. --check asks again and says what differs, and which saved days a drive no longer shows.
    No addresses or positions are kept.

OPTIONS
    --save           take the fingerprints (does not overwrite a day already saved for that drive)
    --check          compare with the saved ones
    --new            ask for the timeline built from what is kept here (the app's /api/timeline?roles=<drive>) instead of the one read from each drive
    --data-dir DIR   Tesla data folder (default: the iCloud Tesla folder)
    --port N         spare port for the app (default 8794)
    -h, --help       this text

EXIT STATUS
    0 everything compared is identical (or saved), 1 something differs or is missing, 2 not ready.

EXAMPLES
    ./reference_timeline.py --save
    ./reference_timeline.py --check
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "Tools", "tesla_app.py")


def fp(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def get(port, path, timeout=170):
    return urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout).read()


def days_of(tl):
    """{day: {minutes, sentry, complete, fp}} from one timeline answer. A day is complete when no item of it is a recording still to be read."""
    by = collections.defaultdict(list)
    for it in tl.get("items", []):
        by[(it.get("start") or "")[:10]].append(it)
    out = {}
    for day, its in by.items():
        if not day:
            continue
        core = [it for it in its if it["kind"] in ("drive", "stopped", "sentry", "recording")]
        complete = all(it["kind"] != "recording" for it in core)
        row = dict(minutes=sum(it.get("n", 0) for it in core if it["kind"] != "sentry"), sentry=sorted(it["ev"] for it in core if it["kind"] == "sentry"), complete=complete)
        if complete:
            row["fp"] = fp(sorted((it["kind"], it.get("start"), it.get("end"), it.get("km"), it.get("n"), it.get("ev")) for it in core))
        out[day] = row
    return out


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--data-dir")
    ap.add_argument("--port", type=int, default=8794)
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args()
    if a.help or not (a.save or a.check):
        print(__doc__)
        return 0
    data = os.path.abspath(os.path.expanduser(a.data_dir or "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
    if not os.path.isdir(data):
        print("Data folder not found: %s (use --data-dir)" % data)
        return 2
    ref_path = os.path.join(data, "_reference", "timeline.json")
    ref = json.load(open(ref_path)) if os.path.exists(ref_path) else {}
    if a.check and not ref.get("drives"):
        print("Nothing saved yet: run --save first.")
        return 2
    srv = subprocess.Popen([sys.executable, APP, "--no-window", "--port", str(a.port), "--data-dir", data], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got = {}
    try:
        for _ in range(60):
            try:
                get(a.port, "/", 5)
                break
            except Exception:
                time.sleep(1)
        roles = [d["role"] for d in json.loads(get(a.port, "/api/teslacam"))["drives"]]
        if not roles:
            print("No TeslaCam drive is connected: nothing to save or check.")
            return 2
        for r in roles:
            got[r] = days_of(json.loads(get(a.port, ("/api/timeline?roles=%s" if a.new else "/api/teslacam/%s/timeline") % r)))
    finally:
        srv.terminate()
    if a.save and a.new:
        print("--new is for --check only: the saved results must come from the existing timeline.")
        return 2
    if a.save:
        ref.setdefault("drives", {})
        ref["saved"] = time.strftime("%Y-%m-%d", time.gmtime())
        n = 0
        for r, days in got.items():
            keep = ref["drives"].setdefault(r, {})
            for d, row in days.items():
                if d not in keep:
                    keep[d] = row
                    n += 1
        os.makedirs(os.path.dirname(ref_path), exist_ok=True)
        json.dump(ref, open(ref_path + ".tmp", "w"), indent=0, sort_keys=True)
        os.replace(ref_path + ".tmp", ref_path)
        print("Saved %d new drive-days (%s); %d complete (fingerprinted)." % (n, ", ".join("%s: %d" % (r, len(v)) for r, v in got.items()),
                                                                              sum(1 for v in ref["drives"].values() for x in v.values() if x.get("complete"))))
        return 0
    same = diff = 0
    problems = []
    for r, saved in ref["drives"].items():
        if r not in got:
            print("drive %s is not connected now: its %d saved days were not compared." % (r, len(saved)))
            continue
        for d, row in saved.items():
            now = got[r].get(d)
            if now is None:
                problems.append("missing: drive %s %s no longer listed" % (r, d))
            elif now["minutes"] != row["minutes"] or now["sentry"] != row["sentry"]:
                problems.append("different: drive %s %s minutes %s -> %s, sentry %d -> %d" % (r, d, row["minutes"], now["minutes"], len(row["sentry"]), len(now["sentry"])))
            elif row.get("complete") and now.get("complete") and row.get("fp") != now.get("fp"):
                problems.append("different: drive %s %s items changed" % (r, d))
            else:
                same += 1
    new = sum(1 for r, days in got.items() for d in days if d not in ref["drives"].get(r, {}))
    print("Compared: %d identical, %d problems, %d days new since the save." % (same, len(problems), new))
    for p in problems[:20]:
        print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
