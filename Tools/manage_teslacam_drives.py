#!/usr/bin/env python3
"""manage_teslacam_drives.py - say what each TeslaCam drive is for; the answer is kept in config.json.

NAME
    manage_teslacam_drives.py - list the connected TeslaCam drives and identify new ones

SYNOPSIS
    manage_teslacam_drives.py [--identify] [--set ID --label WORD --function car|archive|copy] [--forget ID]

DESCRIPTION
    A volume is a TeslaCam drive when it holds a TeslaCam folder shaped like the car's. Which drive it is comes from the volume's own UUID, never
    its name (macOS calls them TESLADRIVE, TESLADRIVE 1, TESLADRIVE 2 in the order they were plugged in). The first time a drive is seen you say
    what it is for, with a one-word name of your own:

        car       goes in the car and records; the source of footage
        archive   the master copy: footage is imported to it and judged against it before anything is deleted
        copy      any other backup or transfer drive

    The answer goes in config.json under teslacam.drives. Nothing is ever written to a drive. Without options this lists the connected drives.

OPTIONS
    --identify                  ask about each connected drive that is not identified yet
    --set ID --label W --function F   record a drive without being asked (ID is the volume UUID shown by the list)
    --forget ID                 remove a drive from the list
    -h, --help                  this text

EXIT STATUS
    0 done, 1 an option was wrong.

EXAMPLES
    ./manage_teslacam_drives.py
    ./manage_teslacam_drives.py --identify

NOTES
    Listing reads each drive's top-level folders only, not its clips. The UUID is also what `diskutil info /Volumes/<name>` calls Volume UUID.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_registry as REG  # noqa: E402
import drive_roles as DR  # noqa: E402


def _show():
    ds = DR.details()
    if not ds:
        print("No TeslaCam drive is connected.")
    else:
        print("%-10s %-8s %-8s %8s  %-38s %s" % ("Name", "For", "Key", "Size", "Volume ID", "Mounted as"))
        for d in ds:
            note = "" if d["known"] else ("   <- not identified yet" if d["role"] == "?" else "   (from its marker folder; no volume ID to go on)")
            print("%-10s %-8s %-8s %6s GB  %-38s %s%s" % (d["label"] or "?", d["function"] or "?", d["role"], d["size_gb"] or "?", d["id"] or "-", d["path"], note))
    gone = [e for e in REG.drives() if e["id"] not in {d["id"] for d in ds}]
    for e in gone:
        print("%-10s %-8s %-8s %6s GB  %-38s (not connected)" % (e["label"], e["function"], e["role"], e.get("size_gb") or "?", e["id"]))


def _ask(d):
    print("\nA TeslaCam drive that has not been seen before:")
    print("  mounted as %s, %s GB, volume ID %s" % (d["path"], d["size_gb"] or "?", d["id"]))
    print("  What is it for?   1 = car (goes in the car and records)   2 = archive (the master copy)   3 = copy (any other backup or transfer drive)")
    fn = {"1": "car", "2": "archive", "3": "copy", "car": "car", "archive": "archive", "copy": "copy"}.get(input("  1, 2 or 3 (Enter to skip): ").strip().lower())
    if not fn:
        print("  Skipped. It stays unidentified and is not read.")
        return
    label = input("  A one-word name for it (e.g. %s): " % ("Archive" if fn == "archive" else "JOWUA")).strip()
    e = REG.register(d["id"], label, fn, d["size_gb"])
    print("  Saved: %s (%s), key %s." % (e["label"], e["function"], e["role"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--identify", action="store_true")
    ap.add_argument("--set")
    ap.add_argument("--label")
    ap.add_argument("--function")
    ap.add_argument("--forget")
    a = ap.parse_args()
    if a.forget:
        print("Removed." if REG.forget(a.forget) else "No such drive in the list.")
        return 0
    if a.set:
        if a.function not in REG.FUNCTIONS:
            print("--function must be one of: %s" % ", ".join(REG.FUNCTIONS))
            return 1
        e = REG.register(a.set, a.label, a.function)
        print("Saved: %s (%s), key %s." % (e["label"], e["function"], e["role"]))
        return 0
    if a.identify:
        todo = [d for d in DR.details() if d["role"] == "?" and d["id"]]
        if not todo:
            print("Every connected TeslaCam drive is identified.")
        elif not sys.stdin.isatty():
            print("%d drive(s) need identifying; run this in a terminal." % len(todo))
            return 0
        for d in todo:
            _ask(d)
        print()
    _show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
