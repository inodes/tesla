#!/usr/bin/env python3
"""drive_actions.py - carry out the footage actions queued on a drive page (REQ-194).

The drive pages (drive_report.py) cannot touch files themselves. Their footage section queues actions and
"Download queue" writes tesla_clip_queue_<time>.json to your Downloads folder. This script reads those files:

    copy    copy the chosen clips (all cameras you ticked) to a folder
    delete  flag the chosen RecentClips minutes in .TeslaCam_tombstones.json (same registry analyse_drives.py and
            sync_dashcam.sh use). Files stay until a purge; add --purge to run the normal purge now.
    save    make a SavedClips entry from RecentClips minutes: SavedClips/<YYYY-MM-DD_hh-mm-ss>/ with the files
            moved in and an event.json (reason user_interaction_dashcam_launcher_action_tapped)

Usage:
  drive_actions.py [QUEUE.json ...] [--apply] [--purge] [--root NAME=TeslaCamDir ...]

  QUEUE.json   queue file(s); default: every ~/Downloads/tesla_clip_queue*.json not yet done
  --apply      really do it. Without it nothing changes: you only see what each action would do (dry run).
  --purge      after flagging deletes, run the standard purge (analyse_drives.purge_flagged_tombstones_across_drives).
               Careful: that purges EVERYTHING flagged in the registry, not just this queue. Dry run lists it first.
  --root       override where a drive lives, e.g. --root "A=/Volumes/Something/TeslaCam" (A Archive, J JOWUA, U Tesla USB).
               Default: found by what is ON each mounted drive (marker folder ARCHIVE_2TB / JOWUA_1TB / TESLA_USB_*, else size),
               because volume names (TESLADRIVE, TESLADRIVE 1, ...) are interchangeable and say nothing. See lib/drive_roles.py.

Finished queue files are renamed to *.done.json (never deleted). Nothing is ever deleted except by --purge.
Delete and save only work on RecentClips minutes on the Archive (master) drive; SavedClips/Sentry are copy-only.
"""
import os as _os, sys as _sys; _sys.path[:0] = [p for p in (_os.path.dirname(_os.path.abspath(__file__)), _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))) if p not in _sys.path]      # the commands beside it, and Tools/ (lib, providers)
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
from lib.footage_actions import run_actions, run_purge  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("queues", nargs="*")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--with-secondary", action="store_true", help="with --purge: also scrub the JOWUA / Tesla USB drives if they are connected (default: Archive only)")
    ap.add_argument("--root", action="append", default=[])
    a = ap.parse_args()
    overrides = dict(r.split("=", 1) for r in a.root if "=" in r)   # role letter -> TeslaCam folder
    qs = a.queues or sorted(p for p in glob.glob(os.path.expanduser("~/Downloads/tesla_clip_queue*.json")) if not p.endswith(".done.json"))
    if not qs:
        print("No queue files found (looked for ~/Downloads/tesla_clip_queue*.json).")
        return 0
    print("%s\n" % ("APPLYING" if a.apply else "DRY RUN - nothing will change (add --apply)"))
    state = {}
    for q in qs:
        data = json.load(open(q, encoding="utf-8"))
        print("== %s (%d action(s))" % (q, len(data.get("actions", []))))
        run_actions(data.get("actions", []), a.apply, overrides, state)
        if a.apply:
            os.rename(q, q[:-5] + ".done.json")
    if a.purge:
        run_purge(a.apply, overrides, a.with_secondary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
