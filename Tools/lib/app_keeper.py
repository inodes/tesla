"""app_keeper.py - keeps the metadata and the stills of every connected TeslaCam drive up to date, in the background.

A drive that is connected is used whether or not the Archive holds the same footage. So while the app runs, whenever a drive is connected (or changed) the
Keeper, one step at a time and only when nothing else is reading:

  1. log        lists the files on the drive into the footage log (which drive holds which file, how big, since when)   - app_footage / teslacam_ledger
  2. events     reads the Sentry and Saved events (when, where, why, which clips)                                       - app_teslacam.events
  3. telemetry  reads the speed, position, steering, pedals and autopilot of every minute not read yet                  - app_teslacam.scan_start
  4. stills     makes the one-second front-camera stills not made yet                                                   - app_footage (needs ffmpeg)

so that a drive page, the timeline and the review still have their data and pictures when the disk is away. Nothing on a drive is changed. It can be paused
(the sidebar) - pause before ejecting a disk - and switched off with app.keeper.background = false or `tesla_app.py --no-background`.

  GET  /api/keeper                   what it is doing now
  POST /api/keeper {action: pause|resume}
"""
import calendar
import json
import os
import threading
import time

import app_config

STAGES = ("log", "events", "telemetry", "stills")
LABEL = dict(log="Listing the files on", events="Reading the events on", telemetry="Reading the telemetry on", stills="Making stills from")
NAMES = {"A": "the Archive", "J": "JOWUA", "U": "the Tesla USB"}
FOLDERS = ("RecentClips", "SavedClips", "SentryClips")


def _mtime(root):
    """The newest change in the top of a TeslaCam folder: clips added to Recent, an event folder added to Saved or Sentry."""
    best = 0.0
    for f in FOLDERS:
        try:
            best = max(best, os.path.getmtime(os.path.join(root, f)))
        except OSError:
            pass
    return best


def _epoch(iso):
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0


class Keeper:
    def __init__(self, data_dir, tc, fg, drv, sentry, roots_fn):
        self.dir, self.tc, self.fg, self.drv, self.sentry, self.roots_fn = data_dir, tc, fg, drv, sentry, roots_fn
        self.paused = False
        self.error = ""
        self._stage = None              # None = nothing to do; else the index into STAGES being worked on
        self._started = False           # a background job of this stage is running (so wait for it)
        self._sig = None                # what the last finished pass was made for
        self._cur = None                # what the running pass is for
        self._done_at = 0.0
        self._roles = []
        self._thread = None
        self._played = 0.0              # when a clip was last served to the page (video playing)

    # ---- settings
    @staticmethod
    def enabled():
        return bool(app_config.get("keeper.background", True))

    # ---- video playing: the drives are left alone while it does (six streams from one disk stall if something else is reading it)
    def playing_now(self):
        """Called whenever the page is sent video."""
        self._played = time.time()
        if self._started and self._stage is not None and self._stage < len(STAGES):      # stop at once, not at the next look
            (self.tc.scan_stop if STAGES[self._stage] == "telemetry" else self.fg.stop)()

    def playing(self):
        return time.time() - self._played < float(app_config.get("keeper.play_wait_s", 30))

    # ---- running
    def start(self):
        if self._thread or not self.enabled():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        time.sleep(3)
        while True:
            try:
                self.tick()
            except Exception as e:      # never let the background work end: say what went wrong and go on with the next pass
                self.error = "%s: %s" % (type(e).__name__, e)
                self._stage, self._started, self._sig = None, False, self._cur
            time.sleep(max(5, float(app_config.get("keeper.interval_s", 20))))

    def busy(self):
        """Something else is reading a drive (a job started from a page, a review)."""
        if self.fg.status().get("running") and not self._started:
            return True
        if self.tc.scan_status().get("running") and not self._started:
            return True
        if (self.drv.status() or {}).get("running"):
            return True
        return any(ix.status().get("running") for ix in list(self.tc.ix.values()))

    def _running_ours(self):
        st = STAGES[self._stage] if self._stage is not None else ""
        if st in ("log", "stills"):
            return bool(self.fg.status().get("running"))
        if st == "telemetry":
            return bool(self.tc.scan_status().get("running"))
        return False

    def tick(self):
        if not self.enabled() or self.paused:
            return
        if self.playing():                  # video is playing: stop what it was doing; the pass starts again from the top afterwards
            if self._started:
                st = STAGES[self._stage]
                (self.tc.scan_stop if st == "telemetry" else self.fg.stop)()
                self._started, self._stage, self._sig, self._done_at = False, None, None, 0.0
            return
        roots = {r: p for r, p in (self.roots_fn() or {}).items() if p}
        self._roles = sorted(roots)
        if not roots:
            self._sig, self._stage, self._started = None, None, False
            return
        if self._started:
            if self._running_ours():
                return
            self._started = False
            self._stage += 1
        elif self.busy():
            return
        if self._stage is None:
            sig = tuple((r, round(_mtime(p))) for r, p in sorted(roots.items()))
            if sig == self._sig or time.time() - self._done_at < float(app_config.get("keeper.pass_gap_s", 300)):
                return
            self._cur, self._stage, self.error = sig, 0, ""
        while self._stage < len(STAGES):
            if self._run_stage(STAGES[self._stage], roots):
                self._started = True
                return
            self._stage += 1
        self._sig, self._stage, self._done_at = self._cur, None, time.time()

    def _run_stage(self, stage, roots):
        """Start the stage; True when a background job was started (the next tick waits for it), False when there was nothing to do or it was done here."""
        if stage == "log":
            scans = self._scans()
            need = {r: p for r, p in roots.items() if _mtime(p) > _epoch((scans.get(r) or {}).get("at"))}
            return bool(need) and bool(self.fg.start("log", need).get("running"))
        if stage == "events":
            for r, p in roots.items():
                for kind in ("sentry", "saved"):
                    if r == "A" and kind == "sentry" and self.sentry is not None:
                        self.sentry.events(p)
                    else:
                        self.tc.events(r, kind, p)
            return False
        if stage == "telemetry":
            return self.tc.scan_todo(roots) > 0 and bool(self.tc.scan_start(roots).get("running"))
        if stage == "stills":
            rep = self.fg.stills_report(roots)
            return bool(rep.get("to_make")) and bool(rep.get("ffmpeg")) and bool(self.fg.start("stills", roots).get("running"))
        return False

    def _scans(self):
        try:
            with open(os.path.join(self.dir, "teslacam_ledger", "scans.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    # ---- what the page shows
    def status(self):
        s = dict(enabled=self.enabled(), paused=self.paused, playing=self.playing(), roles=self._roles, error=self.error, working=self._stage is not None, stage="", text="",
                 done=0, total=0)
        if self._stage is not None and self._stage < len(STAGES):
            st = STAGES[self._stage]
            job = self.tc.scan_status() if st == "telemetry" else self.fg.status() if st in ("log", "stills") else {}
            s.update(stage=st, done=job.get("done", 0), total=job.get("total", 0))
            where = " and ".join(NAMES.get(r, r) for r in self._roles) or "the connected drives"
            s["text"] = "%s %s" % (LABEL[st], where)
        return s

    def pause(self):
        self.paused = True
        if self._started:
            st = STAGES[self._stage]
            (self.tc.scan_stop if st == "telemetry" else self.fg.stop)()
            self._started, self._stage = False, None
        return self.status()

    def resume(self):
        self.paused = False
        self._sig = None
        return self.status()
