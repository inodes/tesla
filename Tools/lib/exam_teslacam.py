"""exam_teslacam.py - the TeslaCam logic as a test, Tessie as the answer sheet.

The TeslaCam list is cut from the clips alone (run_cuts.minute_sections with no Tessie record). Tessie's drives are what the car really did. This marks the first against the second, so a
change to the cutting rules shows at once whether it still comes out the way Drives does.

    sections(stamps, kinds)               -> [(kind, t0, t1)] the clips' own drives and parks, UTC (a drive or park is one stretch of minutes with the same label)
    mark(secs, drives, slack=...)         -> {"ok": [...], "split": [...], "merged": [...], "missed": [...], "extra": [...], "unchecked": [...]}  one entry per Tessie drive / clip drive
    covered(drive, kinds_by_t)            -> True when the clips tell something about at least 90 % of the minutes of the drive (otherwise it is not marked: nothing to judge)

A Tessie drive is "ok" when exactly one clip drive overlaps it, starting within START_SLACK and ending within END_SLACK of it (a clip is a minute: the start may be up to a minute early and the end up to
a minute late). "split" = more than one clip drive inside it, "merged" = a clip drive that holds this drive and another, "missed" = no clip drive (the car moved, the clips say parked),
"extra" = a clip drive with no Tessie drive (a few seconds of moving that Tessie did not log are not counted: MIN_EXTRA_S).

    Tools/teslacam_exam.py [--day 2026-11-04] [--from D --to D] [--kinds kinds.json] [-v]
"""
import bisect

START_SLACK = (-75, 150)     # seconds: the clip drive's start minus Tessie's start
END_SLACK = (-150, 75)       # the clip drive's end minus Tessie's end
MIN_EXTRA_S = 90             # a clip drive shorter than this, with no Tessie drive, is noise
MIN_DRIVE_S = 45             # Tessie's tiny hops (a re-park) shorter than this are not marked


def sections(stamps, kinds, utc, run_cuts, moves=None, marks=None):
    sec = run_cuts.minute_sections(stamps, kinds, moves, 0, marks)
    out = []
    for st in sorted(sec, key=utc):
        kind = "drive" if sec[st].startswith("drive") else "park"
        t0, t1 = utc(st), utc(st) + 60
        if out and out[-1][0] == kind and sec[st] == out[-1][3] and t0 - out[-1][2] <= 75:
            out[-1] = (kind, out[-1][1], t1, sec[st])
        else:
            out.append((kind, t0, t1, sec[st]))
    return [(k, a, b) for k, a, b, _ in out]


def covered(t0, t1, minute_ts, share=0.9):
    """minute_ts: sorted UTC starts of the minutes the clips say something about."""
    n = max(1, int((t1 - t0) // 60) + 1)
    have = bisect.bisect_left(minute_ts, t1) - bisect.bisect_left(minute_ts, t0 - 59)
    return have >= share * n


def _ov(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


def mark(secs, drives, minute_ts):
    """secs: [(kind, t0, t1)] from sections(); drives: [dict(t0, t1)] Tessie's, UTC."""
    cd = [s for s in secs if s[0] == "drive"]
    res = dict(ok=[], split=[], merged=[], missed=[], extra=[], unchecked=[])
    used = set()
    for d in sorted(drives, key=lambda x: x["t0"]):
        if d["t1"] - d["t0"] < MIN_DRIVE_S:
            continue
        if not covered(d["t0"], d["t1"], minute_ts):
            res["unchecked"].append(d)
            continue
        hit = [s for s in cd if _ov(s[1], s[2], d["t0"], d["t1"]) >= 30]
        used.update(hit)
        if not hit:
            res["missed"].append(d)
        elif len(hit) > 1:
            res["split"].append((d, hit))
        else:
            s = hit[0]
            others = [x for x in drives if x is not d and x["t1"] - x["t0"] >= MIN_DRIVE_S and _ov(s[1], s[2], x["t0"], x["t1"]) >= 30]
            if others:
                res["merged"].append((d, s, others))
            elif START_SLACK[0] <= s[1] - d["t0"] <= START_SLACK[1] and END_SLACK[0] <= s[2] - d["t1"] <= END_SLACK[1]:
                res["ok"].append((d, s))
            else:
                res["split"].append((d, [s]))           # the edges are off: reported with the split ones (one clip drive, the wrong length)
    for s in cd:
        if s not in used and s[2] - s[1] >= MIN_EXTRA_S and not any(_ov(s[1], s[2], d["t0"], d["t1"]) > 0 for d in drives):
            if covered(s[1], s[2], minute_ts):
                res["extra"].append(s)
    return res
