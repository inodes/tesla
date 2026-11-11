"""test_stat_memo.py - what was read from a file or folder is kept until it changes (lib/stat_memo.py), and the stills index, event pictures and drive windows built on it.

    python3 -m unittest tests/test_stat_memo.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import stat_memo as SM  # noqa: E402


def old(path, secs=10):
    """Makes a file or folder look last changed `secs` ago (so it counts as settled)."""
    t = time.time() - secs
    os.utime(path, (t, t))


class Memo(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        SM.forget()

    def test_a_file_is_read_once_until_it_changes(self):
        p = os.path.join(self.d, "a.json")
        open(p, "w").write('{"n": 1}')
        old(p)
        calls = []

        def load(path):
            calls.append(path)
            return json.load(open(path))
        self.assertEqual(SM.file(p, load), {"n": 1})
        self.assertEqual(SM.file(p, load), {"n": 1})
        self.assertEqual(len(calls), 1)
        open(p, "w").write('{"n": 22}')                          # another size and time
        self.assertEqual(SM.file(p, load), {"n": 22})
        self.assertEqual(len(calls), 2)

    def test_a_missing_or_broken_file_gives_the_default_and_is_not_kept(self):
        p = os.path.join(self.d, "b.json")
        self.assertIsNone(SM.file(p, lambda q: json.load(open(q))))
        open(p, "w").write("{not json")
        old(p)
        self.assertEqual(SM.file(p, lambda q: json.load(open(q)), {}), {})
        open(p, "w").write('{"ok": true}')
        self.assertEqual(SM.file(p, lambda q: json.load(open(q))), {"ok": True})

    def test_a_file_changed_a_moment_ago_is_read_again(self):
        """The clock a file system stamps with is coarse: a second change in the same instant must not be missed."""
        p = os.path.join(self.d, "c.txt")
        open(p, "w").write("one")
        calls = []
        SM.file(p, lambda q: calls.append(1) or open(q).read())
        SM.file(p, lambda q: calls.append(1) or open(q).read())
        self.assertEqual(len(calls), 2)                           # not settled yet: read both times

    def test_a_folder_is_listed_again_only_when_it_changed(self):
        open(os.path.join(self.d, "x.zip"), "w").write("12345")
        os.mkdir(os.path.join(self.d, "sub"))
        old(self.d)
        first = SM.listing(self.d)
        self.assertEqual(first, {"x.zip": 5, "sub": None})
        self.assertIs(SM.listing(self.d), first)                  # from memory
        open(os.path.join(self.d, "y.zip"), "w").write("1")       # the folder's time changes
        self.assertEqual(SM.listing(self.d), {"x.zip": 5, "sub": None, "y.zip": 1})
        os.remove(os.path.join(self.d, "x.zip"))
        self.assertEqual(sorted(SM.listing(self.d)), ["sub", "y.zip"])
        self.assertEqual(SM.listing(os.path.join(self.d, "nowhere")), {})


class Built(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        SM.forget()

    def test_the_stills_kept_are_known_as_they_are_made_and_removed(self):
        from lib import teslacam_stills as ST
        s = ST.Stills(self.d)
        self.assertEqual(s.stamps(), set())
        for st in ("2026-11-10_09-00-00", "2026-11-10_09-01-00", "2026-11-11_10-00-00"):
            os.makedirs(os.path.dirname(s.path(st)), exist_ok=True)
            open(s.path(st), "wb").write(b"z" * 7)
        self.assertEqual(len(s.stamps()), 3)
        self.assertEqual(s.sizes()["2026-11-11_10-00-00"], 7)
        self.assertEqual(s.remove(["2026-11-10_09-00-00"]), (1, 7))
        self.assertEqual(sorted(s.stamps()), ["2026-11-10_09-01-00", "2026-11-11_10-00-00"])
        open(s.path("2026-11-10_09-02-00"), "wb").write(b"zz")
        self.assertIn("2026-11-10_09-02-00", s.stamps())

    def test_an_events_pictures_and_info_follow_the_folder(self):
        import event_shots as ES
        import event_stills as EV
        sh, ev = ES.EventShots(self.d), EV.EventStills(self.d)
        self.assertEqual((sh.have("sentry", "e1"), sh.size("sentry", "e1"), sh.tried("sentry", "e1"), ev.info("sentry", "e1"), ev.size("sentry", "e1")), (set(), 0, set(), {}, 0))
        os.makedirs(sh.folder("sentry", "e1"))
        open(os.path.join(sh.folder("sentry", "e1"), "front.jpg"), "wb").write(b"j" * 9)
        json.dump(["back"], open(os.path.join(sh.folder("sentry", "e1"), "tried.json"), "w"))
        self.assertEqual((sh.have("sentry", "e1"), sh.size("sentry", "e1"), sh.tried("sentry", "e1")), ({"front"}, 9, {"back"}))
        os.makedirs(ev.folder("sentry", "e1"))
        ev._save_info("sentry", "e1", dict(n=3, cam="front", cams=["front"]))
        open(os.path.join(ev.folder("sentry", "e1"), "front.zip"), "wb").write(b"z" * 11)
        i = ev.info("sentry", "e1")
        self.assertEqual((i["n"], ev.size("sentry", "e1")), (3, 11))
        i["cams"].append("back")                                   # what a caller does to its answer is its own
        self.assertEqual(ev.info("sentry", "e1")["cams"], ["front"])
        ev._save_info("sentry", "e1", dict(n=4, cam="front"))
        self.assertEqual(ev.info("sentry", "e1")["n"], 4)
        sh.remove("sentry", "e1")
        self.assertEqual(sh.have("sentry", "e1"), set())

    def test_the_drive_windows_follow_new_drives_and_new_snapshots(self):
        import teslacam_deletable as TD
        base = os.path.join(self.d, "drives", "deepdive", "2026-11-11")

        def drive(name, a, b):
            os.makedirs(os.path.join(base, name))
            json.dump(dict(start=a, end=b), open(os.path.join(base, name, "drive_summary.json"), "w"))
        drive("d1", "2026-11-11 08:56:20", "2026-11-11 09:10:34")
        w = TD.drive_windows(self.d)
        self.assertEqual([x[2] for x in w], [False])
        drive("d2", "2026-11-11 09:16:51", "2026-11-11 09:26:01")
        os.makedirs(os.path.join(base, "d1", "snapshots"))
        open(os.path.join(base, "d1", "snapshots", "index.json"), "w").write("{}")
        w2 = TD.drive_windows(self.d)
        self.assertEqual([x[2] for x in w2], [True, False])
        self.assertEqual(w2[0][:2], w[0][:2])
        self.assertEqual(TD.drive_windows(os.path.join(self.d, "none")), [])


if __name__ == "__main__":
    unittest.main()
