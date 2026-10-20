"""teslacam_sync.py - everything the app keeps from a TeslaCam clip, taken in one pass over the clip.

A front clip is opened once and in the same worker gives its telemetry (teslacam_timeline) and its 60 stills (teslacam_stills), so the
second read of the file comes from the system cache, not from the drive. The file log (teslacam_ledger) is updated first, the snapshots
of each synced drive (drive_snapshots) afterwards, and what is then safe to delete is reported (teslacam_deletable).

    plan(data, roots)                      what is missing: clips needing telemetry and/or stills, newest first
    run(plan, data, ...)                   makes them, `workers` clips at a time; returns (telemetry read, clips with stills made)
    pending_snapshots(data, since, only)   drive folders whose snapshots are missing or older than the drive summary

A clip on two drives (a copy) is taken once, from the first drive in ORDER (Archive, JOWUA, Tesla USB). Everything is resumable: what is
kept is skipped. Nothing on a drive is changed.
"""
import collections
import concurrent.futures as cf
import datetime
import json
import glob
import os
import time

import app_config
import teslacam_stills as ST
import teslacam_timeline as TL


def _one(stamp, path, do_tel, do_stills, data, width, quality):
    """Worker process: one clip, both jobs."""
    tel, n = None, 0
    if do_tel:
        tel = TL._job(path)
    if do_stills:
        try:
            n = ST.Stills(data, width, quality).make(stamp, path)
        except Exception:                                   # one bad clip must not stop the rest
            n = 0
    return tel, n


def plan(data, roots):
    """dict(tc, stills, tel (scan report), st (stills report), todo=[dict(stamp, role, path, tel, st)] newest first)."""
    import app_teslacam
    tc = app_teslacam.TeslaCam(data)
    rep_t = tc.scan_report(roots)
    stills = ST.Stills(data, app_config.get("stills.width", 400), app_config.get("stills.quality", 6))
    rep_s = ST.plan(stills, roots)
    clips = {}
    for x in tc._scan_todo:
        clips[x["stamp"]] = dict(stamp=x["stamp"], role=x["role"], path=x["path"], tel=True, st=False)
    for x in rep_s["todo"]:
        c = clips.setdefault(x["stamp"], dict(stamp=x["stamp"], role=x["role"], path=x["path"], tel=False, st=False))
        c["st"] = True
    todo = sorted(clips.values(), key=lambda c: c["stamp"], reverse=True)
    return dict(tc=tc, stills=stills, tel=rep_t, st=rep_s, todo=todo)


def run(pl, data, workers=None, stop=None, progress=None, limit=None):
    """Takes the planned clips. progress(n, total, stamp). Returns (telemetry minutes read, clips whose stills were made)."""
    import clip_clock as CC
    store, _ = pl["tc"]._tl("A")
    jobs = pl["todo"][:limit] if limit else pl["todo"]
    w = max(1, int(workers or app_config.get("stills.workers", 3)))
    st = pl["stills"]
    tel_n = still_n = n = 0
    try:
        with cf.ProcessPoolExecutor(w) as ex:
            win, it = collections.deque(), iter(jobs)

            def fill():
                while len(win) < w * 2:
                    j = next(it, None)
                    if j is None:
                        return
                    win.append((j, ex.submit(_one, j["stamp"], j["path"], j["tel"], j["st"], data, st.width, st.quality)))
            fill()
            while win:
                j, fut = win.popleft()
                if stop and stop():
                    fut.cancel()
                    for _j, f in win:
                        f.cancel()
                    break
                tel, made = fut.result()
                if j["tel"] and tel is not None:
                    store.put(j["stamp"], tel)
                    tel_n += 1
                still_n += 1 if made else 0
                n += 1
                if progress:
                    progress(n, len(jobs), j["stamp"])
                fill()
    finally:
        store.flush()
    if tel_n:
        CC.refresh(data, store, wait=True)
    return tel_n, still_n


def snapshots_stale(folder, data=None):
    """True when a drive folder has no snapshots yet, or its telemetry file (Tessie's samples) is newer than them. Rebuilding the drive page does not
    count: it rewrites drive_summary.json with the same content. A drive that had nothing to show (no dashcam telemetry on its route) is looked at
    again only when telemetry for its days (the day before to the day after) has been added since."""
    idx = os.path.join(folder, "snapshots", "index.json")
    if not os.path.exists(idx):
        return True
    src = glob.glob(os.path.join(folder, "drive_deepdive_*.csv")) or [os.path.join(folder, "drive_summary.json")]
    made = os.path.getmtime(idx)
    if made < max(os.path.getmtime(f) for f in src if os.path.exists(f)):
        return True
    try:
        with open(idx, encoding="utf-8") as fh:
            empty = not json.load(fh).get("snapshots")
    except (OSError, ValueError):
        return True
    if empty:
        data = data or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(folder))))
        day = datetime.date.fromisoformat(os.path.basename(folder)[:10])
        for d in (day - datetime.timedelta(days=1), day, day + datetime.timedelta(days=1)):
            f = os.path.join(data, "teslacam_timeline", d.isoformat() + ".json")
            if os.path.exists(f) and os.path.getmtime(f) > made:
                return True
    return False


def pending_snapshots(data, since=None, only=None, force=False):
    """Drive stamps (newest first) whose snapshots/ are missing or older than the drive summary."""
    out = []
    for sj in sorted(glob.glob(os.path.join(data, "drives", "deepdive", "*", "*", "drive_summary.json")), reverse=True):
        folder = os.path.dirname(sj)
        stamp = os.path.basename(folder)
        if only and stamp not in only:
            continue
        if since and stamp[:10] < since:
            continue
        if not force and not snapshots_stale(folder, data):
            continue
        out.append(stamp)
    return out

_W = {}


def _snap_init(data, front, width, quality):
    """Worker process: its own view of the telemetry store and stills (nothing is shared between workers but the files)."""
    import app_teslacam
    _W["data"], _W["front"] = data, front
    _W["tc"] = app_teslacam.TeslaCam(data, None)
    _W["stills"] = ST.Stills(data, width, quality)


def _snap_one(stamp):
    import drive_snapshots as SN
    t = time.time()
    try:
        r = SN.build(_W["data"], stamp, _W["tc"], _W["front"], _W["stills"])
        return stamp, r, None, time.time() - t
    except Exception as e:                                  # one bad drive must not stop the rest
        return stamp, None, "%s: %s" % (type(e).__name__, e), time.time() - t


def run_snapshots(data, stamps, front, workers=None, stop=None, progress=None, note=None, budget=None):
    """Builds the snapshots of these drives (newest first), `workers` at a time. progress(n, total, stamp); note(text) for a drive that failed or had
    nothing to show. Returns dict(done, seconds, video_s, busy_s): the drives finished, wall time, and the seconds spent reading video / building
    (summed over workers) so a slow run shows where its time went."""
    w = max(1, int(workers or app_config.get("stills.workers", 3)))
    st = time.time()
    done, video_s, busy_s, n = 0, 0.0, 0.0, 0
    ex = cf.ProcessPoolExecutor(w, initializer=_snap_init, initargs=(data, front, app_config.get("stills.width", 400), app_config.get("stills.quality", 6)))
    try:
        win, it = collections.deque(), iter(stamps)
        stopped = [False]

        def fill():
            while len(win) < w * 2 and not stopped[0]:
                if budget is not None and time.time() - st > budget:
                    stopped[0] = True
                    if note:
                        note("budget reached - run again to continue")
                    return
                s_ = next(it, None)
                if s_ is None:
                    return
                win.append((s_, ex.submit(_snap_one, s_)))
        fill()
        while win:
            s_, fut = win.popleft()
            if stop and stop():
                fut.cancel()
                for _s, f in win:
                    f.cancel()
                break
            stamp, r, err, secs = fut.result()
            n += 1
            busy_s += secs
            if err:
                if note:
                    note("%s: %s" % (stamp, err))
            else:
                done += 1
                video_s += r.get("video_s", 0.0)
                if r.get("note") and note:
                    note("%s: %s" % (stamp, r["note"]))
            if progress:
                progress(n, len(stamps), stamp)
            fill()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return dict(done=done, seconds=time.time() - st, video_s=video_s, busy_s=busy_s)
