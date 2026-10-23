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


class RunNow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fg, self.tc = Fg(), Tc()
        self.k = AK.Keeper(self.tmp, self.tc, self.fg, Drv(), None, lambda: {"J": self.tmp})
        self.k._thread = object()                       # no background thread in the test: tick() is called by hand

    def passes(self, n=6):
        for _ in range(n):
            self.k.tick()

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


if __name__ == "__main__":
    unittest.main()
