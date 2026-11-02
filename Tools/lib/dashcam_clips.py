"""dashcam_clips.py - TeslaCam clips found on the drives and matched to Tessie's drive points: the clip list (find_clips), the drives searched (TESLACAM_CANDIDATES), the hour offsets clip names can be off by, the clips' save reasons, a cache of read clips (Cache) and one clip's telemetry (Timeline). Used by dashcam_flip_review, drive_report and maintenance tools."""

import bisect
import datetime
import math
import os
from dashcam_series import read_series
import drive_roles as DR

DEFAULT_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
# Default footage folders: the Archive ONLY, found by the marker folder in the drive root (never by mount name - TESLADRIVE / TESLADRIVE 1 / 2 are
# interchangeable). JOWUA and the Tesla USB are opt-in (--with-secondary), see lib/drive_roles.py and AGENTS.md 3.5.1.
TESLACAM_CANDIDATES = DR.teslacam_roots()
OFFSETS = (0, 3600)           # clip names were an hour behind Tessie on some days (AEST vs AEDT)


def hav_km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def find_clips(roots, since):
    """[(start datetime in the clip's own clock, path)] for every front-camera clip since the date, from every TeslaCam folder."""
    out = {}
    if isinstance(roots, str):
        roots = [roots]

    def add(d):
        try:
            it = list(os.scandir(d))
        except OSError:
            return
        for e in it:
            n = e.name
            if n.endswith("-front.mp4") and not n.startswith("._") and n[:10] >= since:
                try:
                    s = datetime.datetime.strptime(n[:19], "%Y-%m-%d_%H-%M-%S")
                except ValueError:
                    continue
                try:
                    sz = e.stat().st_size
                except OSError:
                    sz = 0
                if s not in out or sz > out[s][1]:      # same start in two folders = same footage; keep the longer (not cut short by a save)
                    out[s] = (e.path, sz)

    # RecentClips, SavedClips and SentryClips are one timeline: when the car saves an event, those minutes
    # move out of RecentClips into SavedClips, so only all three together give continuous footage.
    for root in roots:
        add(os.path.join(root, "RecentClips"))
        for sub in ("SavedClips", "SentryClips"):
            try:
                for e in os.scandir(os.path.join(root, sub)):
                    if e.is_dir() and e.name[:10] >= since:
                        add(e.path)
            except OSError:
                pass
    return sorted((k, v[0]) for k, v in out.items())


REASONS = {
    "user_interaction_honk": "you honked",
    "user_interaction_dashcam_icon_tapped": "dashcam icon tapped",
    "user_interaction_dashcam_launcher_action_tapped": "dashcam button tapped",
    "user_interaction_dashcam_panel_save": "dashcam save button",
    "sentry_aware_object_detection": "Sentry saw an object",
    "sentry_aware_accel": "Sentry felt a bump",
    "sentry_locked_handle_pulled": "Sentry: door handle pulled",
    "vehicle_auto_emergency_braking": "automatic emergency braking",
}


class Cache:
    def __init__(self):
        self.d = {}

    def get(self, path):
        if path not in self.d:
            self.d[path] = read_series(path)
        return self.d[path]


class Timeline:
    """prev + current + next clip on one time axis (seconds from the current clip's start)."""

    def __init__(self, clips, starts, ci, cache):
        cur = clips[ci][0]
        self.parts = []  # (t_start, path, S)
        for k in (ci - 1, ci, ci + 1):
            if 0 <= k < len(clips):
                dt = (clips[k][0] - cur).total_seconds()
                if k != ci and abs(dt) > 75:
                    continue
                S = cache.get(clips[k][1])
                if S["n"]:
                    self.parts.append((dt, clips[k][1], S))
        self.t, self.v, self.ap, self.br, self.lat, self.lon, self.hd, self.blk = [], [], [], [], [], [], [], []
        for dt, _p, S in self.parts:
            step = S["dur"] / S["n"]
            for i in range(S["n"]):
                self.t.append(dt + i * step)
                self.v.append(S["v"][i])
                self.ap.append(S["ap"][i])
                self.br.append(S["br"][i])
                self.lat.append(S["lat"][i])
                self.lon.append(S["lon"][i])
                self.hd.append(S["hd"][i])
                self.blk.append(S["blk"][i])
        order = sorted(range(len(self.t)), key=self.t.__getitem__)
        for name in ("t", "v", "ap", "br", "lat", "lon", "hd", "blk"):
            a = getattr(self, name)
            setattr(self, name, [a[i] for i in order])

    def idx(self, t):
        i = bisect.bisect_left(self.t, t)
        if i <= 0:
            return 0
        if i >= len(self.t):
            return len(self.t) - 1
        return i if abs(self.t[i] - t) < abs(self.t[i - 1] - t) else i - 1

    def covers(self, t):
        return bool(self.t) and self.t[0] <= t <= self.t[-1]

    def clip_at(self, t):
        """(path, seconds into that clip) for a time on the axis."""
        for dt, p, S in self.parts:
            if dt <= t < dt + S["dur"]:
                return p, t - dt
        dt, p, S = min(self.parts, key=lambda x: min(abs(t - x[0]), abs(t - x[0] - x[2]["dur"])))
        return p, max(0.0, min(S["dur"] - 0.1, t - dt))
