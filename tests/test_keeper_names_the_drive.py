"""The Keeper names the drive it is working on and reads the events only of drives that changed: with four drives connected and work only on JOWUA, the text says JOWUA and
only JOWUA is marked busy (orange)."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_keeper as AK  # noqa: E402
import drive_roles  # noqa: E402
sys.path.insert(0, HERE)
from test_keeper_run_now import Fg, Tc, Drv  # noqa: E402


class NamesTheDriveTest(unittest.TestCase):
    def setUp(self):
        real = AK.app_config.get
        AK.app_config.get = lambda k, *a, **kw: False if k in ("keeper.stills",) else real(k, *a, **kw)
        self.addCleanup(lambda: setattr(AK.app_config, "get", real))
        self.nm = dict(drive_roles.NAMES)
        drive_roles.NAMES.update(A="Archive", J="JOWUA", L="LaCie", U="TeslaUSB")
        self.addCleanup(lambda: (drive_roles.NAMES.clear(), drive_roles.NAMES.update(self.nm)))
        self.d = {r: tempfile.mkdtemp() for r in "AJLU"}
        self.fg, self.tc = Fg(), Tc()
        self.k = AK.Keeper(tempfile.mkdtemp(), self.tc, self.fg, Drv(), None, lambda: dict(self.d))
        self.k._thread = object()
        self.k.enabled = lambda: True

    def test_the_events_are_read_only_on_the_drive_that_changed(self):
        real = AK._mtime
        AK._mtime = lambda p: 2000 if p == self.d["J"] else 1000
        self.addCleanup(lambda: setattr(AK, "_mtime", real))
        self.k._sig, self.k._done_at = tuple((r, 1000) for r in "AJLU"), time.time() - 10000      # the last pass saw all four; JOWUA has changed since
        for _ in range(6):
            self.k.tick()
        self.assertEqual({r for r, _k in self.tc.ev}, {"J"})

    def test_the_text_names_the_drive_being_read_and_the_ones_after_it(self):
        k = self.k
        k._stage, k._plan = AK.STAGES.index("telemetry"), ["J", "J", "L"]
        k.tc.scan_status = lambda: dict(done=1, total=3)
        s = k.status()
        self.assertEqual(s["text"], "Reading the telemetry on JOWUA (then LaCie)")
        self.assertEqual(s["drive"], "J")
        self.assertEqual(sorted(s["drives"]), ["J", "L"])                       # Archive and TeslaUSB are not in it: no orange
        k.tc.scan_status = lambda: dict(done=2, total=3)
        self.assertEqual(k.status()["text"], "Reading the telemetry on LaCie")  # JOWUA is done

    def test_the_page_marks_only_the_drives_with_work(self):
        js = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()
        i = js.index("const kp=async()")
        blk = js[i:i + 900]
        self.assertIn("Object.entries(k.drives||{})", blk)
        self.assertNotIn("k.roles||[]", blk)


if __name__ == "__main__":
    unittest.main()
