"""teslacam_scan.py - which footage on the connected TeslaCam drives has had its telemetry read, and which has not.

The telemetry (speed, GPS, steering, pedals, autopilot) of every minute of Recent / Saved video is read once into the
shared store (teslacam_timeline.Store, <data>/teslacam_timeline). The store is kept when the video is deleted, so a drive
can still be drawn finely from it. This compares the footage of the drives you pick with the store:

    plan(store, minutes_by_role, front)   -> the report and the list of minutes to read
    read(store, todo, ...)                -> reads them (newest first), then lets the car's clock be learned again

A minute that is on two drives (the same clip copied) is read once, from the first drive in ORDER (the Archive first).
Nothing on a drive is changed. Reading opens each front clip, which is why a drive is only scanned when it is picked.
"""
import drive_roles as DR
import teslacam_timeline as TL

ORDER = "AJU?"


def _day(stamp):
    return stamp[:10]


def plan(store, minutes_by_role, front):
    """minutes_by_role {role: TL.minutes(...)}; front(role, minute) -> path of its front clip.
    Returns dict(drives=[...], todo=[dict(stamp, role, path, t)] newest first, kept, kept_orphans).
    Per drive: minutes, recent, saved, no_front, read, unread, to_read, elsewhere, first, last (days)."""
    roles = sorted(minutes_by_role, key=lambda r: ORDER.index(r) if r in ORDER else 9)
    taken, todo, drives, seen = set(), [], [], set()
    for role in roles:
        mins = minutes_by_role[role]
        d = dict(role=role, name=DR.NAMES.get(role, role), minutes=len(mins), recent=sum(1 for m in mins if m["src"] == "recent"),
                 saved=sum(1 for m in mins if m["src"] == "saved"), no_front=0, read=0, unread=0, to_read=0, elsewhere=0,
                 first=_day(mins[0]["stamp"]) if mins else None, last=_day(mins[-1]["stamp"]) if mins else None)
        for m in mins:
            seen.add(m["stamp"])
            if "front" not in m["cams"]:
                d["no_front"] += 1
            elif store.summary(m["stamp"]) is not None:
                d["read"] += 1
            else:
                d["unread"] += 1
                if m["stamp"] in taken:
                    d["elsewhere"] += 1                 # an earlier drive in ORDER has this minute and reads it
                else:
                    taken.add(m["stamp"])
                    d["to_read"] += 1
                    todo.append(dict(stamp=m["stamp"], role=role, path=front(role, m), t=m["t"]))
        drives.append(d)
    todo.sort(key=lambda x: x["t"], reverse=True)
    kept = store.stamps()
    return dict(drives=drives, todo=[{k: v for k, v in x.items() if k != "t"} for x in todo], to_read=len(todo), kept=len(kept),
                kept_orphans=sum(1 for s in kept if s not in seen))


def read(store, todo, data_dir=None, stop=None, progress=None, limit=None):
    """Reads the telemetry of the planned minutes [(dict stamp, path)] into the store; returns how many were read. Afterwards the car's
    clock is learned again from what is now in the store (clip_clock), so the new minutes can be placed in time."""
    import clip_clock as CC
    jobs = [(x["stamp"], x["path"]) for x in todo][:limit] if limit else [(x["stamp"], x["path"]) for x in todo]
    try:
        n = TL.read_many(store, jobs, stop=stop, progress=progress)
    finally:
        store.flush()
    if data_dir and n:
        CC.refresh(data_dir, store, wait=True)
    return n


def days(first, last):
    """'2026-08-25 to 2026-10-16' (or one date, or '')."""
    if not first:
        return ""
    return first if first == last else "%s to %s" % (first, last)
