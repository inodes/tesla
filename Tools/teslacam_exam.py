#!/usr/bin/env python3
"""teslacam_exam.py - marks the TeslaCam cutting (clips alone) against Tessie's drives, a day or a range at a time. See lib/exam_teslacam.py.

    Tools/teslacam_exam.py --day 2026-11-04 [-v]
    Tools/teslacam_exam.py --from 2026-10-01 --to 2026-11-04 [--kinds ~/Library/Caches/Tesellate/<id>/minute_kind_v2.json]
Minutes whose telemetry was never read have no class (the app reads them in the background); give --kinds to use what the app has kept. A drive the clips do not cover is left unmarked.
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "lib")]
import app_config  # noqa: E402,F401
import app_teslacam as AT  # noqa: E402
import clip_clock as CC  # noqa: E402
import exam_teslacam as EX  # noqa: E402
import run_cuts as RC  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("TESLA_DATA_DIR") or os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
    ap.add_argument("--day")
    ap.add_argument("--from", dest="lo")
    ap.add_argument("--to", dest="hi")
    ap.add_argument("--kinds")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    lo, hi = a.day or a.lo or "0000", a.day or a.hi or "9999"
    tc = AT.TeslaCam(a.data_dir)
    cams, _h, _d = tc.all_minutes()
    store = tc._tl("A")
    import json
    kept = json.load(open(a.kinds)) if a.kinds else {}
    stamps = sorted(s for s in cams if lo <= s[:10] <= hi)
    kinds = {}
    for st in stamps:
        sm = store.summary(st)
        c = kept.get(st) if sm is None else "" if not isinstance(sm, dict) else "go" if sm.get("mov") else "pk"
        if c is not None:
            kinds[st] = c
    drives = []
    with open(os.path.join(a.data_dir, "drives", "drives_master.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                drives.append(dict(t0=int(r["Started At (UTC)"]), t1=int(r["Ended At (UTC)"]), name="%s %s -> %s" % (r["Started At (Local)"][5:16], (r["Starting Saved Location"] or r["Starting Location"])[:24], (r["Ending Saved Location"] or r["Ending Location"])[:24])))
            except (KeyError, ValueError):
                pass
    drives = [d for d in drives if lo <= CC.local_iso(d["t0"])[:10] <= hi]
    minute_ts = sorted(CC.utc(s) for s in kinds)
    marks = {}
    for st, c in kinds.items():                      # a stop in Park inside the driving, from the gear of the minute's telemetry (as the app does)
        if c == "go":
            try:
                ser = store.series(st)
                if ser and ser.get("gear"):
                    marks[st] = RC.stop_marks(ser["gear"])
            except Exception:
                pass
    secs = EX.sections(stamps, kinds, CC.utc, RC, None, marks)
    res = EX.mark(secs, drives, minute_ts)
    n = {k: len(v) for k, v in res.items()}
    judged = n["ok"] + n["split"] + n["merged"] + n["missed"]
    print("Tessie drives judged: %d   ok %d  split/edges %d  merged %d  missed %d   |  clip drives with no Tessie drive: %d   |  not covered by clips: %d" % (judged, n["ok"], n["split"], n["merged"], n["missed"], n["extra"], n["unchecked"]))
    if judged:
        print("score: %.0f%%" % (100.0 * n["ok"] / judged))
    if a.v:
        loc = CC.local_iso
        for k in ("split", "merged", "missed", "extra"):
            for x in res[k]:
                d = x if isinstance(x, dict) else x[0] if isinstance(x[0], dict) else None
                print(" %-7s %s" % (k, ("%s  %s" % (loc(d["t0"])[5:16], d["name"][12:])) if d else "%s - %s clip drive" % (loc(x[1])[5:16], loc(x[2])[11:16])), (["%s-%s" % (loc(s[1])[11:16], loc(s[2])[11:16]) for s in (x[1] if k == "split" else [x[1]])] if k in ("split", "merged") else ""))
    return 0 if not (n["split"] or n["merged"] or n["missed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
