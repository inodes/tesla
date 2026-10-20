#!/usr/bin/env python3
"""
reference_drives.py - save, then later check, what the app shows for drives today, so a clean-up can prove it changed no result.

NAME
    reference_drives.py - reference results for the drives domain

SYNOPSIS
    reference_drives.py --save [--data-dir DIR] [--budget SECONDS]
    reference_drives.py --check [--data-dir DIR] [--budget SECONDS]

DESCRIPTION
    Starts the app on a spare port (read-only: nothing is changed by looking), asks it for the summary, the activity list, the battery
    history, the monthly analytics, and for a spread of drive pages and fused tracks, and keeps only a short fingerprint of each in
    <data>/_reference/drives.json (a file of fingerprints; no addresses or positions). Only things older than the cut-off day are kept,
    because newer drives change as syncs run. --check asks again and says what differs. Both stop after --budget seconds
    and carry on from where they stopped when run again.

OPTIONS
    --save           take the fingerprints (does not overwrite an item already saved)
    --check          compare with the saved ones
    --data-dir DIR   Tesla data folder (default: the iCloud Tesla folder)
    --budget SECONDS stop after this long (default 140)
    --cutoff DAY     only drives, charges and idles before this day (default: a week before the first save)
    -h, --help       this text

EXIT STATUS
    0 everything compared is identical (or saved), 1 something differs, 2 not ready.

EXAMPLES
    ./reference_drives.py --save
    ./reference_drives.py --check
"""
import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "Tools", "tesla_app.py")
PORT = 8793
SAMPLE = 40


def fp(obj):
    b = obj if isinstance(obj, bytes) else json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b).hexdigest()[:16]


def get(path, timeout=150):
    try:
        return urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=timeout).read()
    except Exception as e:
        return ("ERR %s" % type(e).__name__).encode()


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--data-dir")
    ap.add_argument("--budget", type=float, default=140)
    ap.add_argument("--cutoff")
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args()
    if a.help or not (a.save or a.check):
        print(__doc__)
        return 0
    data = os.path.abspath(os.path.expanduser(a.data_dir or "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
    if not os.path.isdir(data):
        print("Data folder not found: %s (use --data-dir)" % data)
        return 2
    ref_path = os.path.join(data, "_reference", "drives.json")
    ref = json.load(open(ref_path)) if os.path.exists(ref_path) else {}
    ref.setdefault("items", {})
    if a.save and not ref.get("cutoff"):
        ref["cutoff"] = a.cutoff or time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
        ref["saved"] = time.strftime("%Y-%m-%d", time.gmtime())
    if a.check and not ref.get("items"):
        print("Nothing saved yet: run --save first.")
        return 2
    cutoff = ref["cutoff"]
    t0 = time.time()
    srv = subprocess.Popen([sys.executable, APP, "--no-window", "--port", str(PORT), "--data-dir", data], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            if get("/", 5)[:3] != b"ERR":
                break
            time.sleep(1)
        got = {}                                                    # what the app says now: key -> fingerprint

        def item(key, make):
            if time.time() - t0 > a.budget:
                return False
            if a.save and key in ref["items"]:
                return True
            got[key] = make()
            return True
        todo_left = 0
        # whole-list answers, kept as one fingerprint per record older than the cut-off
        act = json.loads(get("/api/activity"))
        for r in act:
            day = str(r.get("day") or "")
            if day and day < cutoff:
                key = "activity/%s/%s" % (r.get("k"), r.get("id"))
                if not (a.save and key in ref["items"]):
                    got[key] = fp(r)
        mon = json.loads(get("/api/analytics"))
        for m in mon:
            if str(m.get("month", "")) < cutoff[:7]:
                got["analytics/%s" % m["month"]] = fp(m)
        bat = json.loads(get("/api/battery"))
        rd = bat.get("readings") or []
        got["battery/spec"] = fp(bat.get("spec"))
        got["battery/older_readings"] = fp([x for x in rd if str(x.get("day") or x.get("date") or x.get("t") or "")[:10] < cutoff]) if rd and isinstance(rd[0], dict) else fp(len(rd))
        summ = json.loads(get("/api/summary"))
        got["summary/vehicle"] = fp(summ.get("vehicle"))
        # a spread of older drives: the page and the fused track
        base = os.path.join(data, "drives", "deepdive")
        folders = sorted(d for d in glob.glob(os.path.join(base, "*", "*")) if os.path.basename(d)[:10] < cutoff and os.path.exists(os.path.join(d, "drive_summary.json")))
        sample = folders[::max(1, len(folders) // SAMPLE)][:SAMPLE]
        done_all = True
        pos = int(ref.get("check_pos", 0)) % len(sample) if (a.check and sample) else 0       # a check carries on where the last one stopped
        order = sample[pos:] + sample[:pos]
        n_done = 0
        for d in order:
            day, stamp = os.path.basename(os.path.dirname(d)), os.path.basename(d)
            if not item("page/" + stamp, lambda: fp(get("/data/drives/deepdive/%s/%s/drive.html" % (day, stamp)))):
                done_all = False
                break
            if not item("fuse/" + stamp, lambda: fp(get("/api/drivefuse?id=%s" % stamp))):
                done_all = False
                break
            n_done += 1
        # each drive's summary file (not regenerated by this tool): the stored fact record
        for d in folders:
            stamp = os.path.basename(d)
            key = "file/summary/" + stamp
            if not (a.save and key in ref["items"]):
                try:
                    j = json.load(open(os.path.join(d, "drive_summary.json")))
                    for k in ("render",):
                        j.pop(k, None)
                    got[key] = fp(j)
                except Exception:
                    got[key] = "unreadable"
    finally:
        srv.terminate()
    if a.save:
        ref["items"].update(got)
        os.makedirs(os.path.dirname(ref_path), exist_ok=True)
        json.dump(ref, open(ref_path + ".tmp", "w"), indent=0, sort_keys=True)
        os.replace(ref_path + ".tmp", ref_path)
        pages = [k for k in ref["items"] if k.startswith(("page/", "fuse/"))]
        print("Saved %d fingerprints (%d drive pages and tracks of %d wanted); records before %s." % (len(ref["items"]), len(pages), 2 * len(sample), cutoff))
        print("Complete." if done_all and len(pages) >= 2 * len(sample) else "Not finished: run --save again to carry on.")
        return 0
    if sample:
        ref["check_pos"] = (pos + n_done) % len(sample)
        json.dump(ref, open(ref_path + ".tmp", "w"), indent=0, sort_keys=True)
        os.replace(ref_path + ".tmp", ref_path)
    same = [k for k in got if ref["items"].get(k) == got[k]]
    diff = [k for k in got if k in ref["items"] and ref["items"][k] != got[k]]
    new = [k for k in got if k not in ref["items"]]
    missing = [k for k in ref["items"] if k not in got and not k.startswith(("page/", "fuse/"))]
    print("Compared %d: %d identical, %d different, %d new since the save, %d no longer present." % (len(got), len(same), len(diff), len(new), len(missing)))
    for k in diff[:15]:
        print("  different:", k)
    for k in missing[:5]:
        print("  missing:  ", k)
    if not done_all:
        print("%d of %d pages and tracks compared this time: run --check again to carry on (it resumes)." % (n_done, len(sample)))
    return 1 if diff or missing else 0


if __name__ == "__main__":
    sys.exit(main())
