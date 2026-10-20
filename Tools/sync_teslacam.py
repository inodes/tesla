#!/usr/bin/env python3
"""sync_teslacam.py - keep everything the app needs from the TeslaCam drives, in one command.

NAME
    sync_teslacam.py - file log, telemetry, stills, drive snapshots and the "safe to delete" check, together

SYNOPSIS
    sync_teslacam.py [--data-dir DIR] [--drive ROLE[,ROLE] | --with-secondary] [--run] [--limit N] [--workers N]
                     [--since YYYY-MM-DD] [--no-snapshots] [--budget SECONDS]
                     [--free GB [--keep-days N] [--include-saved] [--delete]]

DESCRIPTION
    One pass does what teslacam_ledger.py --scan, teslacam_scan.py --read, teslacam_stills.py --make, build_drive_snapshots.py and
    check_teslacam_deletable.py do one by one:

      1. file log     which clips are on which drive, and which were deleted
      2. clips        each front clip is opened once and gives its telemetry (speed, GPS, FSD) and its 60 stills together;
                      a clip on two drives is taken once; what is already kept is skipped
      3. snapshots    for every drive synced from Tessie: a still every 30 s along it and a still a second around notable events
                      (needs the drive in drives/deepdive, so run sync_drives_all.sh first for new drives)
      4. report       per drive, what is safe to delete and what is still missing
      5. free space   with --free GB: the oldest video whose telemetry, stills and snapshots are all kept (or, on JOWUA / the Tesla
                      USB, that the Archive already has) is listed until GB are free on each drive named; --delete then removes
                      exactly those files after you type yes. Video that is not yet safe is never offered.

    Without --run you only see what is missing. Everything is resumable: stop with Ctrl-C and run it again. Nothing on a drive is
    changed or deleted.

OPTIONS
    --data-dir DIR       Tesla data folder (default: config.json)
    --drive ROLE[,ROLE]  drives to take: A Archive, J JOWUA, U Tesla USB (default: A)
    --with-secondary     every connected drive
    --run                do the work (otherwise only counts)
    --limit N            with --run, stop the clip step after N clips
    --workers N          clips taken at once (default: app.stills.workers, 3)
    --since YYYY-MM-DD   only snapshot drives starting on or after this date
    --no-snapshots       skip step 3
    --budget SECONDS     stop starting new snapshot drives after this long
    --free GB            make GB free on each drive named: list what can go, oldest first (nothing is deleted without --delete)
    --keep-days N        with --free, never offer the newest N days of video on a drive (default 3)
    --include-saved      with --free, also offer Saved and Sentry clips (default: Recent clips only)
    --delete             with --free, delete the listed files after you confirm
    -h, --help           this text

EXIT STATUS
    0 done, 1 no Archive or no TeslaCam drive found, 130 stopped with Ctrl-C (what was kept stays).

EXAMPLES
    ./sync_teslacam.py --drive A,J
    ./sync_teslacam.py --drive A,J --run
    ./sync_teslacam.py --drive A --free 300
    ./sync_teslacam.py --drive A --free 300 --delete

NOTES
    Only the drives you name are read (AGENTS.md 3.5.1); the Archive is always read for snapshots and for the report. Snapshots are
    cut from the Archive's video. The same work is available in the app.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import app_config  # noqa: E402
import drive_roles as DR  # noqa: E402
import teslacam_ledger as TLG  # noqa: E402
import teslacam_sync as SY  # noqa: E402
from progress import Bar  # noqa: E402


def _n(x):
    return "{:,}".format(x)


def _gb(b):
    return "%.1f GB" % (b / 1024 ** 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir")
    ap.add_argument("--drive")
    ap.add_argument("--with-secondary", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--since")
    ap.add_argument("--no-snapshots", action="store_true")
    ap.add_argument("--budget", type=float, default=1e9)
    ap.add_argument("--free", type=float)
    ap.add_argument("--keep-days", type=int, default=3)
    ap.add_argument("--include-saved", action="store_true")
    ap.add_argument("--delete", action="store_true")
    a = ap.parse_args()
    if a.data_dir:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
    else:
        from tessie_api_common import resolve_tessie_data_home
        data = resolve_tessie_data_home()
    import clip_clock as CC
    CC.configure(data)
    found = {r: p for p, r in DR.discover() if r != "?"}
    new = [d for d in DR.unidentified() if d["id"]]
    if new:
        print("%d connected TeslaCam drive(s) not identified yet and left alone - run ./manage_teslacam_drives.py --identify" % len(new))
    want = [r.strip().upper() for r in a.drive.split(",")] if a.drive else (list(found) if a.with_secondary else ["A"])
    for r in want:
        if r not in found:
            print("%s is not connected." % DR.NAMES.get(r, r))
    roots = {r: found[r] for r in want if r in found}
    if not roots:
        print("No TeslaCam drive to take.")
        return 1
    import teslacam_stills as ST
    if not ST.have_ffmpeg():
        print("ffmpeg is not installed.")
        return 1
    # 1. file log
    if a.run:
        print("1. File log")
        for r in sorted(roots, key=lambda x: "AJU?".index(x) if x in "AJU?" else 9):
            res = TLG.Ledger(data).scan(r, roots[r])
            print("   %-8s %s file(s): %s new, %s gone" % (DR.NAMES.get(r, r), _n(res["files"]), _n(res["new"]), _n(res["gone"])))
    # 2. clips
    print("2. Clips (telemetry and stills together)")
    pl = SY.plan(data, roots)
    tel = pl["tel"]["drives"]
    st = {d["role"]: d for d in pl["st"]["drives"]}
    print("   %-10s %9s %12s %12s  %s" % ("Drive", "Clips", "Telemetry", "Stills", "to make"))
    for d in tel:
        s = st.get(d["role"], {})
        print("   %-10s %9s %12s %12s" % (d["name"], _n(s.get("clips", 0)), _n(d["to_read"]), _n(s.get("to_make", 0))))
    todo = pl["todo"]
    print("   %s clip(s) to take." % _n(len(todo)))
    firsts = [d["first"] for d in tel if d["first"]]
    since = a.since or (min(firsts) if firsts else None)                # drives before the first footage have nothing to show
    snaps = [] if a.no_snapshots else SY.pending_snapshots(data, since)
    print("3. Snapshots: %s drive(s) without snapshots%s." % (_n(len(snaps)), " (skipped)" if a.no_snapshots else ""))
    if not a.run and a.free is None:
        print("Run again with --run to do it.")
        return 0
    t0 = time.time()
    if not a.run:
        todo, snaps = [], []
    total = min(len(todo), a.limit) if a.limit else len(todo)
    if total:
        print("Taking %s clip(s), newest first. Ctrl-C stops; what is kept stays." % _n(total))
        bar = Bar(total, "   Clips")
        try:
            tel_n, still_n = SY.run(pl, data, a.workers, progress=lambda n, tot, stamp: bar.update(n, stamp.replace("_", " ")), limit=a.limit)
        except KeyboardInterrupt:
            bar.note("")
            print("Stopped. What was kept stays.")
            return 130
        bar.done()
        print("   telemetry for %s minute(s), stills for %s clip(s)." % (_n(tel_n), _n(still_n)))
    # 3. snapshots
    if snaps:
        import app_teslacam
        import drive_snapshots as SN
        tc = app_teslacam.TeslaCam(data, None)
        front = SN.front_clips(tc, found.get("A")) if found.get("A") else {}
        t1 = time.time()
        print("3. Snapshots for %s drive(s)" % _n(len(snaps)))
        done = 0
        sbar = Bar(len(snaps), "   Snapshots")
        try:
            for i, stamp in enumerate(snaps):
                if time.time() - t1 > a.budget:
                    sbar.note("   budget reached - run again to continue")
                    break
                sbar.update(i, stamp)
                try:
                    r = SN.build(data, stamp, tc, front, pl["stills"])
                except Exception as e:                      # one bad drive must not stop the rest
                    sbar.note("   %s: %s: %s" % (stamp, type(e).__name__, e))
                    continue
                done += 1
            sbar.update(len(snaps), "")
            sbar.done()
        except KeyboardInterrupt:
            sbar.note("")
            print("Stopped. What was kept stays.")
            return 130
        print("   %s drive(s) done." % _n(done))
    # 4. report
    rep = None
    if "A" in roots:
        import teslacam_deletable as TD
        print("4. Safe to delete")
        rep = TD.report(data, roots, detail=a.free is not None)
        import check_teslacam_deletable as CK
        for role in sorted(rep, key=lambda r: "AJU".index(r) if r in "AJU" else 9):
            CK.print_role(role, rep[role], 0)
    # 5. free space
    if a.free is not None and rep:
        import teslacam_purge as PG
        kinds = ("recent", "saved", "sentry") if a.include_saved else ("recent",)
        print("5. Free %s GB on each drive" % ("%g" % a.free))
        for role in sorted(roots, key=lambda x: "AJU".index(x) if x in "AJU" else 9):
            if role not in rep:
                continue
            free = PG.free_bytes(roots[role])
            pl = PG.plan(rep, role, int(a.free * 1024 ** 3) - free, a.keep_days, kinds)
            name = DR.NAMES.get(role, role)
            print("   %-8s %s free now." % (name, _gb(free)))
            if pl["need"] <= 0:
                print("   %-8s already has %g GB free." % ("", a.free))
                continue
            if not pl["minutes"]:
                print("   %-8s nothing is safe to delete yet (%s of video is not yet safe)." % ("", _gb(pl["skipped_unsafe_bytes"])))
                continue
            print("   %-8s %s minute(s), %s, from %s to %s%s" % ("", _n(pl["minutes"]), _gb(pl["bytes"]), pl["first"].replace("_", " "), pl["last"].replace("_", " "),
                                                               "" if pl["enough"] else "  - only %s of the %s needed is safe to delete" % (_gb(pl["bytes"]), _gb(pl["need"]))))
            if not a.delete:
                continue
            if input("   Delete these %s file(s) from %s? Type yes: " % (_n(sum(len(d["paths"]) for d in pl["items"])), name)).strip().lower() != "yes":
                print("   Not deleted.")
                continue
            t2 = time.time()
            dbar = Bar(len(pl["items"]), "   Deleting")
            nf, fb, errs = PG.delete(pl, progress=lambda n, tot, stamp: dbar.update(n, stamp.replace("_", " ")))
            dbar.done()
            print("   %s file(s) deleted, %s freed, %s error(s)." % (_n(nf), _gb(fb), _n(len(errs))))
            for e in errs[:5]:
                print("     " + e)
            TLG.Ledger(data).scan(role, roots[role])
    return 0


if __name__ == "__main__":
    sys.exit(main())
