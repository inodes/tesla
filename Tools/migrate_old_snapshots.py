#!/usr/bin/env python3
"""migrate_old_snapshots.py - replace the old flip-review snapshots in the drive folders with the kept stills.

NAME
    migrate_old_snapshots.py - move old-style E###.jpg snapshots (and snippets) into teslacam_stills, then remove them

SYNOPSIS
    migrate_old_snapshots.py [--data-dir DIR] [--apply]

DESCRIPTION
    Older drive pages left, in each drives/deepdive/<day>/<drive>/clips/ folder, a contact sheet E###.jpg (one still a
    second around a speed change) and a short video E###.mp4. The kept stills (teslacam_stills, one zip of 60 stills per
    front clip) replace them. For every old snapshot set, each second it shows is looked up in the kept stills:

        already kept              nothing to do
        video is on a connected   the clip's stills are taken from the video (teslacam_stills), the better copy
        drive
        video is gone everywhere  the still is cut out of the old contact sheet and kept in the clip's zip (400 px wide)

    Only when every second of a set is kept is the old sheet removed. The old E###.mp4 is removed only when its source
    clip is still on a connected drive (otherwise it is the last video of that moment and is left in place).
    The drives are only listed and read, never changed. Without --apply nothing is written or removed.

OPTIONS
    --data-dir DIR   Tesla data folder (default: config.json)
    --apply          do it (otherwise only the plan is shown)
    -h, --help       this text

EXIT STATUS
    0 done (or planned), 1 no ffmpeg or no data folder.

EXAMPLES
    ./migrate_old_snapshots.py
    ./migrate_old_snapshots.py --apply
"""
import argparse
import collections
import datetime
import glob
import io
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_roles as DR  # noqa: E402
import teslacam_files as TF  # noqa: E402
import teslacam_stills as ST  # noqa: E402
import app_config  # noqa: E402

COLS, ROWS = 5, 6                    # the old contact sheet layout (dashcam_flip_review.py)
OFFSETS = tuple(range(-12, 16))      # second of each cell relative to the speed change
FMT = "%Y-%m-%d_%H-%M-%S"


def _stamp(dt):
    return dt.strftime(FMT)


def _records(data):
    out = {}
    for f in glob.glob(os.path.join(data, "drives", "analysis", "dashcam_flip_review", "cache7", "E*.json")):
        try:
            r = json.load(open(f))
        except (OSError, ValueError):
            continue
        r.setdefault("id", os.path.basename(f)[:-5])
        if "clip" in r and "onset_local" in r:
            out[r["id"]] = r
    return out


def _clip_of(starts, t):
    """The front clip (start datetime, from `starts` sorted) that holds clock time t, or None."""
    lo = None
    for s in starts:
        if s <= t:
            lo = s
        else:
            break
    if lo is not None and (t - lo).total_seconds() < 60:
        return lo
    return None


def _dark(cell):
    ex = cell.convert("L").resize((16, 10)).getextrema()
    return ex[1] < 40


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.data_dir:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
    else:
        from tessie_api_common import resolve_tessie_data_home
        data = resolve_tessie_data_home()
    if not data or not ST.have_ffmpeg():
        print("No data folder or no ffmpeg.")
        return 1
    from PIL import Image
    stills = ST.Stills(data, app_config.get("stills.width", 400), app_config.get("stills.quality", 6))

    # front clips on every connected drive (listing only)
    on_drive = {}
    for p, role in DR.discover():
        files, ok = TF.walk(p)
        for f in files:
            if f["cam"] == "front":
                cur = on_drive.get(f["stamp"])
                if cur is None or TF.ORDER[f["src"]] < TF.ORDER[cur[1]["src"]]:
                    on_drive[f["stamp"]] = (p, f)
    known = sorted(set(on_drive) | stills.stamps())
    starts_by_day = collections.defaultdict(list)
    for s in known:
        starts_by_day[s[:10]].append(datetime.datetime.strptime(s, FMT))
    recs = _records(data)

    sets = []
    for sheet in sorted(glob.glob(os.path.join(data, "drives", "deepdive", "*", "*", "clips", "E[0-9][0-9][0-9].jpg"))):
        eid = os.path.basename(sheet)[:-4]
        sets.append((eid, sheet, recs.get(eid)))
    print("%d old snapshot sets in the drive folders" % len(sets))

    n_kept = n_make = n_cut = n_skip = 0
    rm_sheets, rm_mp4, make_clips, cuts, needs = [], [], {}, [], {}
    for eid, sheet, r in sets:
        if not r:
            print("  %s: no record of which clip it came from - left alone" % eid)
            n_skip += 1
            continue
        onset = datetime.datetime.strptime(r["onset_local"], "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=r.get("clip_offset_h", 0))
        im = Image.open(sheet).convert("RGB")
        cw, ch = im.size[0] // COLS, im.size[1] // ROWS
        need_cut, ok_set, vid_gone, seen = [], True, False, []
        for j, rel in enumerate(OFFSETS):
            t = onset + datetime.timedelta(seconds=rel)
            cell = im.crop(((j % COLS) * cw, (j // COLS) * ch, (j % COLS + 1) * cw, (j // COLS + 1) * ch))
            if _dark(cell):
                continue                                   # the old sheet had no footage for this second
            s = _clip_of(sorted(starts_by_day.get(t.strftime("%Y-%m-%d"), []) + starts_by_day.get((t - datetime.timedelta(days=1)).strftime("%Y-%m-%d"), [])), t)
            if s is None:                                  # no clip known there (video and stills both gone): clips are a minute apart from the record's own clip
                m0 = datetime.datetime.strptime(r["clip"].replace("-front.mp4", ""), FMT)
                s = m0 + datetime.timedelta(seconds=60 * int((t - m0).total_seconds() // 60))
            st, sec = _stamp(s), int((t - s).total_seconds())
            seen.append((st, sec))
            if stills.get(st, sec) is not None:
                n_kept += 1
            elif st in on_drive:
                make_clips[st] = on_drive[st]
                n_make += 1
            else:
                need_cut.append((st, sec, cell))
                vid_gone = True
        for st, sec, cell in need_cut:
            cuts.append((eid, st, sec, cell))
            n_cut += 1
        src_stamp = r["clip"].replace("-front.mp4", "")
        if ok_set:
            rm_sheets.append(sheet)
            needs[sheet] = seen
            mp4 = sheet[:-4] + ".mp4"
            if os.path.isfile(mp4) and src_stamp in on_drive:
                rm_mp4.append(mp4)
    print("seconds already kept: %d | to take from video: %d (%d clips) | to cut from old sheets: %d | sets left alone: %d" % (
        n_kept, n_make, len(make_clips), n_cut, n_skip))
    print("old sheets removable: %d | old mp4 removable (source still on a drive): %d" % (len(rm_sheets), len(rm_mp4)))
    if not a.apply:
        print("Plan only. Run with --apply to do it.")
        return 0

    for st, (p, f) in sorted(make_clips.items()):
        n = stills.make(st, TF.front_path(p, f))
        print("  stills %s: %d" % (st, n))
    bycl = collections.defaultdict(list)
    for eid, st, sec, cell in cuts:
        bycl[st].append((sec, cell))
    for st, items in bycl.items():
        dest = stills.path(st)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zipfile.ZipFile(dest, "a", zipfile.ZIP_STORED) as z:
            have = set(z.namelist())
            for sec, cell in items:
                name = "%02d.jpg" % sec
                if name in have:
                    continue
                w = stills.width
                c = cell.resize((w, int(cell.size[1] * w / cell.size[0])))
                b = io.BytesIO()
                c.save(b, "JPEG", quality=85)
                z.writestr(name, b.getvalue())
                have.add(name)
        print("  cut from old sheets into %s: %d second(s)" % (os.path.basename(dest), len(items)))
    # remove only sets whose every second is now kept
    removed, removed_mp4, kept = 0, 0, 0
    for sheet in rm_sheets:
        if any(stills.get(st, sec) is None for st, sec in needs[sheet]):
            kept += 1                                       # a second could not be kept: the old sheet stays
            print("  kept %s: not every second could be saved" % os.path.basename(sheet))
            continue
        os.remove(sheet)
        removed += 1
        mp4 = sheet[:-4] + ".mp4"
        if mp4 in rm_mp4 and os.path.isfile(mp4):
            os.remove(mp4)
            removed_mp4 += 1
    print("removed %d old sheet(s) and %d old video snippet(s); %d left in place" % (removed, removed_mp4, kept))
    return 0


if __name__ == "__main__":
    sys.exit(main())
