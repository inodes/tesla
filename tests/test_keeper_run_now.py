"""app_keeper.Keeper.run_now(): one full pass (stills included) from a page, even with the background reading switched off.

    python3 -m unittest tests/test_keeper_run_now.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_keeper as AK  # noqa: E402


class Fg:
    def __init__(self):
        self.calls = []

    def status(self):
        return {}

    def start(self, kind, roots, by=None):
        self.calls.append(kind)
        return dict(running=False)

    def stills_report(self, roots):
        self.calls.append("stills_report")
        return dict(to_make=0, ffmpeg=True)

    def stop(self):
        pass


class Tc:
    def __init__(self):
        self.ev = []

    def scan_status(self):
        return dict(running=False)

    def scan_todo(self, roots):
        return 0

    def events(self, r, kind, p):
        self.ev.append((r, kind))
        return [], True

    def ix(self):
        return {}


class Drv:
    def status(self):
        return {}


class Pict:
    def __init__(self):
        self.started, self.alive = 0, False

    def pictures_start(self, roots):
        self.started += 1
        return False

    def pictures_running(self):
        return self.alive

    def stop(self):
        pass

    def job(self):
        return {}


class RunNow(unittest.TestCase):
    def setUp(self):
        real = AK.app_config.get                         # these tests are about the switches being off; the defaults (on) are checked below
        AK.app_config.get = lambda k, *a, **kw: False if k in ("keeper.background", "keeper.stills") else real(k, *a, **kw)
        self.addCleanup(lambda: setattr(AK.app_config, "get", real))
        self.tmp = tempfile.mkdtemp()
        self.fg, self.tc = Fg(), Tc()
        self.k = AK.Keeper(self.tmp, self.tc, self.fg, Drv(), None, lambda: {"J": self.tmp})
        self.k._thread = object()                       # no background thread in the test: tick() is called by hand

    def passes(self, n=6):
        for _ in range(n):
            self.k.tick()

    def test_a_drive_plugged_in_is_read_without_waiting_for_the_gap(self):
        import time as _t
        self.k.enabled = lambda: True
        self.k._sig, self.k._done_at = (("A", 1),), _t.time()          # a pass just finished with only the Archive
        self.passes()
        self.assertIn(("J", "saved"), self.tc.ev)                        # JOWUA is new: a pass over it ran now

    def test_the_same_drives_wait_for_the_gap(self):
        import time as _t
        self.k.enabled = lambda: True
        self.k._sig, self.k._done_at = (("J", 999),), _t.time()         # J was there at the last pass (only its folders changed)
        self.k.roots_fn = lambda: {"J": self.tmp}
        real = AK._mtime
        AK._mtime = lambda p: 999 if p == self.tmp else real(p)
        try:
            self.passes()
        finally:
            AK._mtime = real
        self.assertEqual(self.tc.ev, [])

    def test_the_defaults_make_thumbnails_by_themselves(self):
        AK.app_config.get = AK.app_config.get.__closure__[0].cell_contents     # the real reader, back for this test
        self.assertTrue(AK.app_config.get("keeper.background"))
        self.assertTrue(AK.app_config.get("keeper.stills"))

    def test_off_does_nothing(self):
        self.passes()
        self.assertEqual(self.fg.calls, [])
        self.assertFalse(self.k.status()["enabled"])

    def test_run_now_does_one_full_pass_with_stills(self):
        self.k.run_now()
        self.assertTrue(self.k.status()["enabled"])
        self.passes()
        self.assertIn("stills_report", self.fg.calls)    # the stills stage ran although app.keeper.stills is off
        self.assertEqual(sorted(self.tc.ev), [("J", "saved"), ("J", "sentry")])
        self.assertFalse(self.k._once)                   # and it stops after the one pass
        self.assertFalse(self.k.status()["enabled"])

    def test_each_drive_gets_its_share_of_the_running_job(self):
        self.k._plan = ["A", "J", "J", "A", "J"]         # newest first: the order the job takes them
        self.assertEqual(self.k._by_drive("telemetry", 3), {"A": dict(done=1, total=2), "J": dict(done=2, total=3)})
        self.assertEqual(self.k._by_drive("telemetry", 0), {"A": dict(done=0, total=2), "J": dict(done=0, total=3)})
        self.k._plan = ["A", "J"]                         # the log: one step a drive
        self.assertEqual(self.k._by_drive("log", 1), {"A": dict(done=1, total=1), "J": dict(done=0, total=1)})

    def test_a_pass_asked_for_from_a_page_does_not_wait_for_video(self):
        self.k.playing_now()
        self.assertTrue(self.k.playing())                 # background reading gives way to video
        self.k.run_now()
        self.assertFalse(self.k.playing())                # a button does not: the page has stopped its videos
        self.k.playing_now()
        self.assertFalse(self.k.status()["playing"])

    def test_a_stopped_job_reports_what_each_drive_got_done(self):
        got = []
        self.k.on_progress = lambda st, d: got.append((st, d))
        self.k._plan = ["J", "J", "A"]
        self.k._stage, self.k._started = AK.STAGES.index("telemetry"), True
        self.tc.scan_status = lambda: dict(running=True, done=2, total=3)
        self.tc.scan_stop = lambda: None
        self.k.pause()
        self.assertEqual(got, [("telemetry", {"J": 2, "A": 0})])

    def test_the_pictures_of_events_are_made_after_the_stills_and_by_make_stills(self):
        self.k.pictures = Pict()
        self.k.run_now(["stills"])
        self.assertEqual(self.k._only, ("stills", "pictures"))
        self.passes()
        self.assertEqual(self.k.pictures.started, 1)

    def test_not_asked_for_they_are_not_made(self):
        self.k.pictures = Pict()
        self.k.run_now(["log"])
        self.passes()
        self.assertEqual(self.k.pictures.started, 0)

    def test_a_page_button_ends_a_pass_left_under_way(self):
        self.k._stage, self.k._started, self.k._once, self.k._only = AK.STAGES.index("telemetry"), False, True, ("log",)     # a pass left marked as under way
        self.assertEqual(self.k.stand_down(wait=0), "")
        self.assertIsNone(self.k._stage)
        self.assertFalse(self.k._once)
        self.assertIsNone(self.k._only)

    def test_a_page_button_stops_a_running_stage_and_waits_for_it(self):
        got, state = [], dict(run=True)
        self.k.on_progress = lambda st, d: got.append(st)
        self.k._plan = ["J"]
        self.k._stage, self.k._started = AK.STAGES.index("telemetry"), True
        self.tc.scan_status = lambda: dict(running=state["run"], done=1, total=3)
        self.tc.scan_stop = lambda: state.update(run=False)
        self.assertEqual(self.k.stand_down(wait=2), "")
        self.assertEqual(got, ["telemetry"])
        self.assertIsNone(self.k._stage)

    def test_what_else_is_reading_the_drives_is_named(self):
        self.k.drv = type("D", (), {"status": lambda s: dict(running=True)})()
        self.assertEqual(self.k.stand_down(wait=0), "A drive review")
        self.k.drv = Drv()
        self.fg.status = lambda: dict(running=True, kind="stills")
        self.assertEqual(self.k.stand_down(wait=0), "The stills job")

    def test_no_pass_starts_while_a_job_from_a_page_runs(self):
        self.k.run_now()
        self.k.page_job = lambda: True
        self.passes()
        self.assertEqual(self.fg.calls, [])
        self.k.page_job = lambda: False
        self.passes()
        self.assertIn("stills_report", self.fg.calls)


if __name__ == "__main__":
    unittest.main()
