"""clip_keep.ClipKeep: what is kept from each clip's video, and deleting / moving the video once it is all kept."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import clip_keep as CK  # noqa: E402
import teslacam_deletable as TD  # noqa: E402
import teslacam_ledger as TLG  # noqa: E402

LIVE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone=None)
S1, S2 = "2026-10-16_15-40-07", "2026-10-16_15-41-07"


class FakeStore:
    def __init__(self, read):
        self.read = read

    def summary(self, st):
        return dict(vmax=60) if st in self.read else None


class FakeTC:
    def __init__(self, read, scanned):
        self.store, self.sc = FakeStore(read), scanned

    def _tl(self, role):
        return self.store

    def _rv(self):
        return SimpleNamespace(scanned=lambda: {s: 0 for s in self.sc})


class FakeFG:
    def __init__(self, stills):
        self.stills = SimpleNamespace(stamps=lambda: set(stills))


class KeepTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.data = os.path.join(self.t, "data")
        self.roots = {r: os.path.join(self.t, r, "TeslaCam") for r in "AJ"}
        for r, root in self.roots.items():
            os.makedirs(os.path.join(root, "SavedClips", "ev1"))
        day = {}
        for st in (S1, S2):                                                      # a Saved clip of two minutes, on JOWUA only
            for cam in ("front", "back"):
                day["saved|ev1|%s-%s" % (st, cam)] = dict(size=10, drives=dict(J=dict(LIVE)))
                with open(os.path.join(self.roots["J"], "SavedClips", "ev1", "%s-%s.mp4" % (st, cam)), "wb") as f:
                    f.write(b"x" * 10)
        led = os.path.join(self.data, "teslacam_ledger")
        os.makedirs(led)
        with open(os.path.join(led, "2026-10-16.json"), "w") as f:
            json.dump(day, f)
        with open(os.path.join(led, "scans.json"), "w") as f:
            json.dump(dict(J=dict(at="x", files=4, bytes=40, ok=True)), f)
        CC.configure(self.data)
        real = TD.drive_windows
        TD.drive_windows = lambda d: [(0, 2 ** 40, True)]                        # one drive window covering everything, snapshots built
        self.addCleanup(lambda: setattr(TD, "drive_windows", real))

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def keep(self, read=(), stills=(), scanned=()):
        return CK.ClipKeep(self.data, FakeTC(set(read), set(scanned)), FakeFG(stills))

    def test_a_clip_with_nothing_kept_is_not_ready_and_says_what_is_missing(self):
        r = self.keep().status()["clips"]["saved"]["ev1"]
        self.assertEqual((r["n"], r["meta"], r["stills"], r["signs"], r["ready"]), (2, [0, 2], [0, 2], [0, 0], False))
        self.assertEqual(r["bytes"], {"J": 40})
        self.assertEqual(r["size"], 40)

    def test_everything_kept_makes_it_ready(self):
        r = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1, S2]).status()["clips"]["saved"]["ev1"]
        self.assertTrue(r["ready"])
        self.assertEqual(r["signs"], [2, 2])                                      # both minutes went 60 km/h: both had their signs read

    def test_signs_count_only_minutes_that_were_driven_fast_enough(self):
        r = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1]).status()["clips"]["saved"]["ev1"]
        self.assertEqual(r["signs"], [1, 2])
        self.assertFalse(r["ready"])

    def test_delete_is_refused_until_it_is_all_kept_then_removes_only_the_video(self):
        k = self.keep(read=[S1], stills=[S1, S2])
        out = k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots)
        self.assertIn("not all kept", out["error"])
        self.assertTrue(os.path.exists(os.path.join(self.roots["J"], "SavedClips", "ev1", "%s-front.mp4" % S1)))
        k = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1, S2])
        k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots)
        k._thread.join(5)
        self.assertEqual(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")), [])
        j = k.job()
        self.assertEqual((j["done"], j["freed"], j["errors"]), (4, 40, []))
        self.assertEqual(k.status(force=True)["clips"]["saved"]["ev1"]["bytes"], {})      # the log knows at once

    def test_move_copies_to_the_archive_first_and_then_removes_it_from_the_drive(self):
        k = self.keep()
        out = k.start("move", "J", [dict(kind="saved", id="ev1")], self.roots)                # nothing kept yet: a move is still allowed, nothing is lost
        self.assertNotIn("error", out)
        k._thread.join(5)
        self.assertEqual(sorted(os.listdir(os.path.join(self.roots["A"], "SavedClips", "ev1"))), sorted(["%s-%s.mp4" % (st, c) for st in (S1, S2) for c in ("front", "back")]))
        self.assertEqual(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")), [])
        self.assertEqual(k.status(force=True)["clips"]["saved"]["ev1"]["bytes"], {"A": 40})

    def test_a_copy_that_the_archive_already_holds_may_be_deleted_from_jowua_at_once(self):
        led = TLG.Ledger(self.data)
        for st in (S1, S2):
            for cam in ("front", "back"):
                led.mark("A", "saved|ev1|%s-%s" % (st, cam), 10, True)
        k = self.keep()
        out = k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots)
        self.assertNotIn("error", out)
        k._thread.join(5)
        self.assertEqual(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")), [])

    def test_a_drive_that_is_not_connected_or_the_archive_itself_is_refused_for_a_move(self):
        k = self.keep()
        self.assertIn("not connected", k.start("delete", "U", [dict(kind="saved", id="ev1")], self.roots)["error"])
        self.assertIn("Archive", k.start("move", "A", [dict(kind="saved", id="ev1")], self.roots)["error"])


if __name__ == "__main__":
    unittest.main()
