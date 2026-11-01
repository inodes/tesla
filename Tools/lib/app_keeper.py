"""app_keeper.py - keeps the metadata and the stills of every connected TeslaCam drive up to date, in the background.

A drive that is connected is used whether or not the Archive holds the same footage. So while the app runs, whenever a drive is connected (or changed) the
Keeper, one step at a time and only when nothing else is reading:

  1. log        lists the files on the drive into the footage log (which drive holds which file, how big, since when)   - app_footage / teslacam_ledger
  2. events     reads the Sentry and Saved events (when, where, why, which clips)                                       - app_teslacam.events
  3. telemetry  reads the speed, position, steering, pedals and autopilot of every minute not read yet                  - app_teslacam.scan_start
  4. stills     only if app.keeper.stills is true (default false): makes the one-second front-camera stills not made yet     - app_footage (ffmpeg)

so that a drive page, the timeline and the review still have their data and pictures when the disk is away. Nothing on a drive is changed. It can be paused
(the sidebar) - pause before ejecting a disk - and off unless switched on with app.keeper.background = true in config.json (`tesla_app.py --no-background` forces it off).

  GET  /api/keeper                   what it is doing now
  POST /api/keeper {action: pause|resume|run, stages: [...]}      run = one pass now (all four steps including stills, or only the named stages), whether or not the background reading is switched on
"""
import calendar
import json
import os
import threading
import time

import app_config

STAGES = ("log", "events", "telemetry", "stills", "pictures")
LABEL = dict(log="Listing the files on", events="Reading the events on", telemetry="Reading the telemetry on", stills="Making stills from", pictures="Saving event pictures from")
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
        self.on_progress = None         # f(stage, {role: done}) when a job ends or is stopped: what is left to do is then shown at once, and worked out afresh after
        self._plan = []                 # the drive each item of the running job belongs to, in the order the job takes them (what each drive's progress is counted from)
        self._thread = None
        self._once = False              # a pass asked for from a page (Sync footage): runs once, stills included, then stops
        self._only = None               # that pass is only these stages (the Update log / Read telemetry / Make stills buttons)
        self._played = 0.0              # when a clip was last served to the page (video playing)
        self.page_job = lambda: False   # True while a job asked for from a page (Update on the TeslaCam page) is reading the drives: the Keeper does not start a pass meanwhile
        self.pictures = None            # lib/clip_keep.ClipKeep: pictures_start(roots) / pictures_running() / stop(): the full-size pictures of events, made after the stills

    # ---- settings
    @staticmethod
    def enabled():
        return bool(app_config.get("keeper.background", False))

    # ---- video playing: the drives are left alone while it does (six streams from one disk stall if something else is reading it)
    def playing_now(self):
        """Called whenever the page is sent video. A pass asked for from a page (a button) does not give way to it: the page stops its videos when the button is pressed."""
        self._played = time.time()
        if self._started and not self._once and self._stage is not None and self._stage < len(STAGES):      # stop at once, not at the next look
            self._stop_stage(STAGES[self._stage])

    def playing(self):
        return not self._once and time.time() - self._played < float(app_config.get("keeper.play_wait_s", 30))

    # ---- running
    def start(self):
        if self._thread or not (self.enabled() or self._once):
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def run_now(self, stages=None):
        """One pass over the connected drives now - log, events, telemetry and the stills, or only `stages` - even when the background reading is off."""
        if self._stage is not None:         # a pass is under way: let it finish (the buttons are disabled meanwhile)
            return self.status()
        self.paused, self._played = False, 0.0
        only = tuple(x for x in (stages or ()) if x in STAGES)
        self._only = (only + ("pictures",) if "stills" in only and "pictures" not in only else only) or None      # Make stills also saves the pictures of events
        self._once, self._sig, self._done_at, self.error = True, None, 0.0, ""
        self.start()
        return self.status()

    def _loop(self):
        time.sleep(15)                  # the window's first pages go first
        while True:
            try:
                self.tick()
            except Exception as e:      # never let the background work end: say what went wrong and go on with the next pass
                self.error = "%s: %s" % (type(e).__name__, e)
                self._stage, self._started, self._sig = None, False, self._cur
            time.sleep(5 if self._once else max(5, float(app_config.get("keeper.interval_s", 20))))

    def _stop_stage(self, st):
        if st == "telemetry":
            self.tc.scan_stop()
        elif st == "pictures":
            if self.pictures:
                self.pictures.stop()
        else:
            self.fg.stop()

    def holder(self):
        """What is reading the drives, in words ('' when nothing): a job started from a page, the telemetry scan or a drive review."""
        if self.fg.status().get("running") and not self._started:
            return "The %s" % ("stills job" if (self.fg.status().get("kind") == "stills") else "footage log job")
        if self.tc.scan_status().get("running") and not self._started:
            return "The telemetry reading"
        if (self.drv.status() or {}).get("running"):
            return "A drive review"
        return ""

    def stand_down(self, wait=8.0):
        """A page asks for the drives (Update, Move, Delete): the pass under way, started by the Keeper itself, ends (it starts again from the top by itself afterwards, from
        what is then left) and its jobs are waited for. Returns what is still reading the drives, '' when they are free."""
        if self._stage is not None:
            if self._started and self._stage < len(STAGES):
                st = STAGES[self._stage]
                self._stop_stage(st)
                self._report(st)
            self._started, self._stage, self._sig, self._done_at, self._once, self._only = False, None, None, 0.0, False, None
        end = time.time() + wait
        while time.time() < end and (self.busy() or self._running_ours_any()):
            time.sleep(0.25)
        return self.holder()

    def _running_ours_any(self):
        """A job of any stage is still winding down (the Keeper has let go of it)."""
        return bool(self.fg.status().get("running") or self.tc.scan_status().get("running") or (self.pictures and self.pictures.pictures_running()))

    def busy(self):
        """Something else is reading a drive (a job started from a page, a review)."""
        if self.fg.status().get("running") and not self._started:
            return True
        if self.tc.scan_status().get("running") and not self._started:
            return True
        return bool((self.drv.status() or {}).get("running"))

    def _running_ours(self):
        st = STAGES[self._stage] if self._stage is not None else ""
        if st in ("log", "stills"):
            return bool(self.fg.status().get("running"))
        if st == "telemetry":
            return bool(self.tc.scan_status().get("running"))
        if st == "pictures":
            return bool(self.pictures and self.pictures.pictures_running())
        return False

    def tick(self):
        if not (self.enabled() or self._once) or self.paused:
            return
        if self.playing():                  # video is playing: stop what it was doing; the pass starts again from the top afterwards
            if self._started:
                st = STAGES[self._stage]
                self._stop_stage(st)
                self._report(st)
                self._started, self._stage, self._sig, self._done_at = False, None, None, 0.0
            return
        roots = {r: p for r, p in (self.roots_fn() or {}).items() if p}
        self._roles = sorted(roots)
        if not roots:
            self._sig, self._stage, self._started, self._once, self._only = None, None, False, False, None
            return
        if self._started:
            if self._running_ours():
                return
            self._started = False
            self._report(STAGES[self._stage])
            self._stage += 1
        elif self.busy() or self.page_job():
            return
        if self._stage is None:
            sig = tuple((r, round(_mtime(p))) for r, p in sorted(roots.items()))
            new_drive = {r for r, _m in sig} - {r for r, _m in (self._sig or ())}          # a drive plugged in since the last pass: read now, not after the gap
            if sig == self._sig or (not new_drive and time.time() - self._done_at < float(app_config.get("keeper.pass_gap_s", 300))):
                return
            self._cur, self._stage, self.error = sig, 0, ""
        while self._stage < len(STAGES):
            if self._run_stage(STAGES[self._stage], roots):
                self._started = True
                return
            self._stage += 1
        self._sig, self._stage, self._done_at = self._cur, None, time.time()
        self._once, self._only = False, None

    def _run_stage(self, stage, roots):
        """Start the stage; True when a background job was started (the next tick waits for it), False when there was nothing to do or it was done here."""
        if self._only and stage not in self._only:
            return False
        if stage == "log":
            scans = self._scans()
            need = roots if self._only else {r: p for r, p in roots.items() if _mtime(p) > _epoch((scans.get(r) or {}).get("at"))}
            self._plan = sorted(need, key=lambda r: "AJU?".index(r) if r in "AJU?" else 9)       # the order Footage._run_log takes them
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
            if self.tc.scan_todo(roots) <= 0:
                return False
            self._plan = [x["role"] for x in getattr(self.tc, "_scan_todo", [])]
            return bool(self.tc.scan_start(roots, reuse=True).get("running"))
        if stage == "stills":
            if not (app_config.get("keeper.stills", False) or self._once):      # stills decode video (ffmpeg): only ever on request (Sync footage, the Footage panel or the command)
                return False
            rep = self.fg.stills_report(roots)
            self._plan = [x["role"] for x in getattr(self.fg, "_todo", [])]
            return bool(rep.get("to_make")) and bool(rep.get("ffmpeg")) and bool(self.fg.start("stills", roots, by="keeper").get("running"))
        if stage == "pictures":
            if not (app_config.get("keeper.stills", False) or self._once) or self.pictures is None:      # ffmpeg again: the same switch as the stills
                return False
            self._plan = []
            return bool(self.pictures.pictures_start(roots))
        return False

    def _scans(self):
        try:
            with open(os.path.join(self.dir, "teslacam_ledger", "scans.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    # ---- what the page shows
    def status(self):
        s = dict(enabled=self.enabled() or self._once, only=list(self._only or ()), paused=self.paused, playing=self.playing(), roles=self._roles, error=self.error, working=self._stage is not None, stage="", text="",
                 done=0, total=0)
        if self._stage is not None and self._stage < len(STAGES):
            st = STAGES[self._stage]
            job = self._job_of(st)
            s.update(stage=st, done=job.get("done", 0), total=job.get("total", 0))
            where = " and ".join(NAMES.get(r) or __import__("drive_roles").NAMES.get(r, r) for r in self._roles) or "the connected drives"
            s["text"] = "%s %s" % (LABEL[st], where)
            s["drives"] = self._by_drive(st, s["done"])
        return s

    def _job_of(self, st):
        if st == "telemetry":
            return self.tc.scan_status()
        if st in ("log", "stills"):
            return self.fg.status()
        if st == "pictures" and self.pictures:
            j = self.pictures.job()
            return dict(done=j.get("step_done", 0), total=j.get("step_total", 0))
        return {}

    def _report(self, stage):
        """A job ended or was stopped: tell what each drive got done, so the page shows the numbers left at once (they are worked out again afterwards)."""
        if not self.on_progress:
            return
        job = self._job_of(stage)
        try:
            self.on_progress(stage, {r: d["done"] for r, d in self._by_drive(stage, job.get("done", 0)).items()})
        except Exception:
            pass

    def _by_drive(self, stage, done):
        """{role: dict(done, total)} for the job running now: each drive's share of it, counted from the order the job takes its items in (newest first for minutes and
        clips, drive by drive for the log). The page shows it as a bar on each drive's row."""
        out = {}
        for i, r in enumerate(self._plan):
            d = out.setdefault(r, dict(done=0, total=0))
            d["total"] += 1
            if i < done:
                d["done"] += 1
        return out

    def pause(self):
        self.paused = True
        if self._started:
            st = STAGES[self._stage]
            self._stop_stage(st)
            self._report(st)
            self._started, self._stage = False, None
        return self.status()

    def resume(self):
        self.paused, self._played = False, 0.0
        self._sig = None
        return self.status()
