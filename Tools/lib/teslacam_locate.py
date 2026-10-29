"""teslacam_locate.py - where the footage of a minute or an event can be read right now: the drive side of teslacam_ingest.py.

The footage log (teslacam_ledger.py) says which drives hold which files, connected or not; `roots` says which of them are mounted now
({role: that drive's TeslaCam folder}). Locator turns the two into the `locate` functions the ingest asks for, so the ingest itself never
mentions a drive.
"""
import os

import drive_roles as DR

ORDER = "AJU?"                  # a minute held by several drives is read from the first of these that is connected
KIND_DIR = {"sentry": "SentryClips", "saved": "SavedClips"}


def front_path(teslacam, key):
    """The front clip named by a footage-log key ('recent||<stamp>-front' or 'saved|<event>|<stamp>-front') in a drive's TeslaCam folder."""
    src, ev, name = key.split("|", 2)
    sub = "RecentClips" if src == "recent" else os.path.join("SavedClips", ev)
    return os.path.join(teslacam, sub, name + ".mp4")


def day_minutes(ledger, day):
    """[dict(stamp, key, src, roles)] the front-camera minutes of Recent and Saved clips the log knows for `day`, oldest first. `roles` = the drives
    that still hold it (not gone). A minute in both Recent and a Saved event is listed once, as Recent."""
    out = {}
    for key, rec in ledger.day(day).items():
        if key.count("|") != 2 or not key.endswith("-front"):
            continue
        src, ev, name = key.split("|", 2)
        if src not in ("recent", "saved"):
            continue
        roles = sorted((r for r, d in (rec.get("drives") or {}).items() if not d.get("gone")), key=DR.rank)
        if not roles:
            continue
        stamp = name[:-len("-front")]
        if stamp not in out or (src == "recent" and out[stamp]["src"] != "recent"):
            out[stamp] = dict(stamp=stamp, key=key, src=src, roles=roles)
    return [out[s] for s in sorted(out)]


class Locator:
    def __init__(self, ledger, roots):
        self.led, self.roots = ledger, roots

    def minute(self, m):
        """Path of the front clip of a day_minutes() entry on the first connected drive that holds it, or None."""
        root = next((self.roots[r] for r in m["roles"] if self.roots.get(r)), None)
        return front_path(root, m["key"]) if root else None

    def event(self, kind, ev, stamp, cam):
        """Path of one camera's clip of a Sentry / Saved event on the first connected drive that holds it, or None."""
        rec = self.led.day(stamp[:10]).get("%s|%s|%s-%s" % (kind, ev["id"], stamp, cam)) or {}
        for r in sorted(rec.get("drives") or {}, key=DR.rank):
            if not rec["drives"][r].get("gone") and self.roots.get(r):
                return os.path.join(self.roots[r], KIND_DIR[kind], ev["id"], "%s-%s.mp4" % (stamp, cam))
        return None
