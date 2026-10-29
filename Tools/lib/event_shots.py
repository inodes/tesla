"""event_shots.py - full-size pictures of the moment an event happened, kept after the video is gone.

    <data>/teslacam_event_shots/<saved|sentry>/<event folder>/<camera>.jpg      one frame at the moment of the event, as the camera recorded it (no scaling)
    <data>/teslacam_event_shots/<saved|sentry>/<event folder>/shots.json        dict(clip, sec, t, reason)  which clip and second they were cut from

Which cameras:
    Horn pressed (a Saved clip)              all six: nothing says which direction the horn was warning about
    Sentry event, camera known               the camera that saw it (event.json "camera"; 0/1 front, 5 left repeater, 6 right repeater, 7 back)
    Sentry event, camera not known           all six, for the same reason
    anything else                            none (the one-second stills are enough)

The moment is the event's own time (event.json "timestamp", the car's clock like the clip names) inside the one-minute clip that holds it. Needs ffmpeg.
Other events read nothing from a drive; only make() opens video.

    wanted(kind, ev, cams_in_clip)    -> [camera]   the cameras that should have a picture (only those that have video for that minute)
    moment(ev, stamps)                -> (clip stamp, second) or None
    EventShots(data).have(kind, id)   -> {camera} that are kept
    EventShots(data).make(kind, ev, paths, quality)  paths = {camera: video file of the moment's clip}; returns the cameras made
"""
import json
import os
import subprocess

import app_config
import clip_clock as CC
import teslacam_stills as ST

DIRNAME = "teslacam_event_shots"
CAMS = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")
EVCAM = {"0": "front", "1": "front", "5": "left_repeater", "6": "right_repeater", "7": "back"}
HORN = "user_interaction_honk"
CLIP_S = 60


def cams_for(kind, ev):
    """The cameras an event wants a picture from (before looking at which videos exist)."""
    if kind == "saved":
        return list(CAMS) if (ev.get("reason") or "") == HORN else []
    if kind == "sentry":
        c = EVCAM.get(str(ev.get("cam") if ev.get("cam") is not None else ""))
        return [c] if c else list(CAMS)
    return []


def moment(ev, stamps):
    """(stamp of the clip holding the event, second inside it): the last clip that starts at or before the event. None when the event is before every clip or has no time."""
    try:
        t = CC.utc(ev.get("ts") or "")
    except (ValueError, TypeError):
        return None
    best = None
    for st in stamps:
        try:
            s = CC.utc(st)
        except (ValueError, TypeError):
            continue
        if s <= t + 0.5 and (best is None or s > best[0]):
            best = (s, st)
    if best is None:
        return None
    return best[1], int(max(0, min(CLIP_S - 1, round(t - best[0]))))


def wanted(kind, ev, cams_in_clip):
    return [c for c in cams_for(kind, ev) if c in cams_in_clip]


class EventShots:
    def __init__(self, data_dir):
        self.root = os.path.join(data_dir, DIRNAME)

    def folder(self, kind, eid):
        return os.path.join(self.root, kind, eid)

    def have(self, kind, eid):
        try:
            return {fn[:-4] for fn in os.listdir(self.folder(kind, eid)) if fn.endswith(".jpg") and fn[:-4] in CAMS}
        except OSError:
            return set()

    def tried(self, kind, eid):
        """The cameras whose picture was asked for and could not be cut (no frame came out of the video): not asked for again."""
        try:
            with open(os.path.join(self.folder(kind, eid), "tried.json"), encoding="utf-8") as fh:
                j = json.load(fh)
                return {c for c in j if c in CAMS} if isinstance(j, list) else set()
        except (OSError, ValueError):
            return set()

    def remove(self, kind, eid):
        """Deletes everything kept for the event; returns the bytes freed."""
        d = self.folder(kind, eid)
        freed = 0
        try:
            for fn in os.listdir(d):
                fp = os.path.join(d, fn)
                try:
                    freed += os.path.getsize(fp)
                    os.remove(fp)
                except OSError:
                    pass
            os.rmdir(d)
        except OSError:
            pass
        return freed

    def info(self, kind, eid):
        try:
            with open(os.path.join(self.folder(kind, eid), "shots.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def path(self, kind, eid, cam):
        p = os.path.join(self.folder(kind, eid), cam + ".jpg")
        return p if cam in CAMS and os.path.isfile(p) else ""

    def make(self, kind, ev, stamp, sec, paths, stop=None, quality=None):
        """Cuts the frame at `sec` of each camera's clip in `paths` ({camera: file}) that has no picture yet; returns the cameras made.
        A drive that stops answering raises teslacam_stills.DriveStalled."""
        q = int(quality if quality is not None else app_config.get("event_shots.quality", 2))
        out = self.folder(kind, ev["id"])
        os.makedirs(out, exist_ok=True)
        made = []
        for cam, src in sorted(paths.items(), key=lambda x: CAMS.index(x[0]) if x[0] in CAMS else 9):
            if (stop and stop()) or cam not in CAMS or os.path.isfile(os.path.join(out, cam + ".jpg")):
                continue
            tmp = os.path.join(out, cam + ".part.jpg")
            try:
                ST.run_ffmpeg(["ffmpeg", "-v", "error", "-y", "-ss", str(sec), "-i", src, "-frames:v", "1", "-q:v", str(q), tmp])
                if not (os.path.isfile(tmp) and os.path.getsize(tmp) > 0):         # the moment is past the end of a clip cut short: its last frame is the nearest picture
                    ST.run_ffmpeg(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.5", "-i", src, "-frames:v", "1", "-q:v", str(q), tmp])
                if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                    os.replace(tmp, os.path.join(out, cam + ".jpg"))
                    made.append(cam)
            except (subprocess.SubprocessError, OSError):
                pass
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            if cam not in made and not (stop and stop()) and not os.path.isfile(os.path.join(out, cam + ".jpg")):
                miss = self.tried(kind, ev["id"]) | {cam}
                with open(os.path.join(out, "tried.json"), "w", encoding="utf-8") as fh:
                    json.dump(sorted(miss), fh)
        if made:
            with open(os.path.join(out, "shots.json"), "w", encoding="utf-8") as fh:
                json.dump(dict(clip=stamp, sec=sec, reason=ev.get("reason") or "", t=ev.get("ts") or ""), fh)
        return made
