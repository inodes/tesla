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
import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_roles as DR  # noqa: E402

CAMS = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")
REASON = "user_interaction_dashcam_launcher_action_tapped"


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.1f %s" % (n, u) if u != "B" else "%d B" % n
        n /= 1024.0


def resolve_root(item, overrides):
    """TeslaCam folder of the drive an item names, found by the drive's ROLE (what is on it), never by its volume name.
    A queue item without a role (an old file) is refused rather than guessed."""
    role = item.get("role")
    if role in overrides:
        return overrides[role]
    return DR.find_root(role) if role in ("A", "J", "U") else None


def files_of(root, item):
    """[(path, size)] of the camera files an item names, that exist."""
    out = []
    for cam in item.get("cams") or CAMS:
        p = os.path.join(root, "%s-%s.mp4" % (item["rel"], cam))
        if os.path.isfile(p):
            out.append((p, os.path.getsize(p)))
    return out


# ------------------------------------------------------------------ copy
def do_copy(act, roots, apply):
    dest = os.path.expanduser(act.get("dest") or "~/Movies/TeslaCam exports/%s" % act.get("drive", "drive"))
    n = b = 0
    for it in act["items"]:
        root = roots.get(it["role"])
        if not root:
            print("   ! the %s drive is not mounted - skipped %s" % (DR.NAMES.get(it.get("role"), "unknown"), it["rel"]))
            continue
        for p, sz in files_of(root, it):
            tgt = os.path.join(dest, os.path.basename(p))
            if os.path.exists(tgt) and os.path.getsize(tgt) == sz:
                continue
            n += 1
            b += sz
            if apply:
                os.makedirs(dest, exist_ok=True)
                shutil.copy2(p, tgt)
    print("   copy: %d file(s), %s -> %s%s" % (n, human(b), dest, "" if apply else "   [dry run]"))
    return n


# ------------------------------------------------------------------ delete (flag)
def do_delete(act, roots, apply, state):
    import analyse_drives as AD
    for it in act["items"]:
        if it.get("src") != "recent":
            print("   ! %s is not RecentClips - delete only handles RecentClips; skipped" % it["rel"])
            return 0
        if it.get("role") != "A":
            print("   ! %s is not on the Archive drive - delete only handles the Archive; skipped" % it["rel"])
            return 0
    drives = sorted({it.get("role") for it in act["items"]})
    dirs = [roots[d] for d in drives if roots.get(d)]
    if not dirs:
        print("   ! no mounted drive for this action - skipped")
        return 0
    master = AD.resolve_master_teslacam_dir(dirs)
    if not master:
        print("   ! the Archive (master) drive is not among the mounted drives - nothing flagged")
        return 0
    state["master"] = master
    data = AD.load_tombstones(master)
    tombs = data.setdefault("tombstones", {})
    known = list(data.get("known_drives", ["VOL_JOWUA", "VOL_TESLA_USB"]))
    now = datetime.datetime.now().isoformat()
    n = b = 0
    for it in act["items"]:
        fl = files_of(master, it)
        names = [os.path.basename(p) for p, _ in fl]
        size = sum(s for _, s in fl)
        key = it["rel"]                                  # RecentClips/<minute>, the key analyse_drives.py uses
        if not names:
            print("   . %s: no files on the Archive - skipped" % key)
            continue
        n += len(names)
        b += size
        tombs[key] = {"status": "flagged", "flagged_at": now, "deleted_at": now, "cat": "Recent", "reason": "drive_page_delete",
                      "folder": "RecentClips", "files": names, "est_bytes": size, "files_count": len(names), "pending_drives": list(known)}
    print("   delete: %d file(s), %s flagged in .TeslaCam_tombstones.json%s" % (n, human(b), "" if apply else "   [dry run]"))
    if apply and n:
        AD.save_tombstones(data, master)
        state["flagged"] = state.get("flagged", 0) + n
    return n


# ------------------------------------------------------------------ save as SavedClips
def do_save(act, roots, apply):
    ev = act["event"]
    t = datetime.datetime.strptime(ev["timestamp"], "%Y-%m-%dT%H:%M:%S")
    drives = {it.get("role") for it in act["items"]}
    if drives != {"A"}:
        print("   ! save only works on the Archive drive - skipped")
        return 0
    if len(drives) != 1 or any(it.get("src") != "recent" for it in act["items"]):
        print("   ! save needs RecentClips minutes from one drive - skipped")
        return 0
    root = roots.get(next(iter(drives)))
    if not root:
        print("   ! drive not mounted - skipped")
        return 0
    folder = os.path.join(root, "SavedClips", t.strftime("%Y-%m-%d_%H-%M-%S"))
    if os.path.exists(folder):
        print("   ! %s already exists - skipped" % folder)
        return 0
    moves = []
    for it in act["items"]:
        for p, sz in files_of(root, it):
            moves.append((p, os.path.join(folder, os.path.basename(p)), sz))
    if not moves:
        print("   ! none of the files exist on this drive - skipped")
        return 0
    print("   save: %d file(s), %s -> %s%s" % (len(moves), human(sum(m[2] for m in moves)), folder, "" if apply else "   [dry run]"))
    if not apply:
        return len(moves)
    os.makedirs(folder)
    for src, dst, _ in moves:
        os.rename(src, dst)
    event = {"timestamp": ev["timestamp"], "city": ev.get("city", ""), "street": ev.get("street", ""),
             "est_lat": str(ev.get("est_lat", "")), "est_lon": str(ev.get("est_lon", "")), "reason": REASON, "camera": str(ev.get("camera", "0"))}
    with open(os.path.join(folder, "event.json"), "w", encoding="utf-8") as fh:
        json.dump(event, fh, indent="\t", ensure_ascii=False)
    fronts = sorted(d for _, d, _ in moves if d.endswith("-front.mp4"))
    if fronts and shutil.which("ffmpeg"):                  # thumb.png from the last front clip, like the car makes
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "30", "-i", fronts[-1], "-frames:v", "1", "-vf", "scale=480:-2",
                        os.path.join(folder, "thumb.png")], stdin=subprocess.DEVNULL, check=False)
    try:                                                    # keep the archive catalog in step so the move is not read as a deletion
        import analyse_drives as AD
        AD.scan_or_update_drive_catalog(root, root, force_rescan=True)
    except Exception as e:
        print("   (catalog not refreshed: %s)" % e)
    return len(moves)


def run_actions(actions, apply, overrides=None, state=None):
    """Carry out (or, without apply, only describe) a list of queued actions. Prints what it does; the app server captures that."""
    overrides = overrides or {}
    state = {} if state is None else state
    for act in actions:
        roots = {}
        for it in act["items"]:
            roots[it.get("role")] = resolve_root(it, overrides)
        print(" - %s %s (%d minute(s), drive page %s)" % (act["type"], act.get("label", ""), len(act["items"]), act.get("drive", "")))
        try:
            if act["type"] == "copy":
                do_copy(act, roots, apply)
            elif act["type"] == "delete":
                do_delete(act, roots, apply, state)
            elif act["type"] == "save":
                do_save(act, roots, apply)
            else:
                print("   ! unknown action type")
        except Exception as e:
            print("   ! failed: %s" % e)


def run_purge(apply, overrides=None, with_secondary=False):
    import analyse_drives as AD
    overrides = overrides or {}
    # Archive only by default: JOWUA / the USB are scrubbed the next time they are connected (their tombstones stay pending).
    dirs = [v for v in (overrides.get("A") or DR.find_root("A"),) if v]
    if with_secondary:
        dirs += [v for v in (overrides.get(r) or DR.find_root(r) for r in "JU") if v]
    print("\nPurge of everything flagged in the registry%s:" % ("" if apply else " (dry run)"))
    nf, nb, nr = AD.purge_flagged_tombstones_across_drives(dirs, dry_run=not apply)
    print("   %d file(s), %s, %d tombstone(s) retired" % (nf, human(nb), nr))


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
