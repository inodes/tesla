#!/usr/bin/env python3
"""test_clip_files.py - the footage as files (lib/clip_files.py): which addresses resolve, and a copy of a range of them into a new folder.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_clip_files.py
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import clip_files as CF  # noqa: E402


def safe_join(root, rel):
    p = os.path.realpath(os.path.join(root, rel))
    r = os.path.realpath(root)
    return p if (p == r or p.startswith(r + os.sep)) else None


class ClipFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "TeslaCam")
        os.makedirs(os.path.join(self.root, "RecentClips"))
        for s in ("2026-11-04_09-28-05", "2026-11-04_09-29-05"):
            for c in ("front", "back"):
                with open(os.path.join(self.root, "RecentClips", "%s-%s.mp4" % (s, c)), "wb") as f:
                    f.write(b"x" * 1000)
        self.fr = lambda role: self.root if role == "A" else None

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_only_connected_existing_videos_inside_the_drive(self):
        urls = ["/footage/A/RecentClips/2026-11-04_09-28-05-front.mp4", "/footage/A/RecentClips/2026-11-04_09-28-05-front.mp4",
                "/footage/A/RecentClips/nope-front.mp4", "/footage/J/RecentClips/2026-11-04_09-28-05-back.mp4",
                "/footage/A/../../etc/passwd.mp4", "/footage/A/RecentClips/2026-11-04_09-29-05-back.mp4?x=1", "/api/x"]
        got = CF.resolve(urls, self.fr, safe_join)
        self.assertEqual([os.path.basename(p) for p in got], ["2026-11-04_09-28-05-front.mp4", "2026-11-04_09-29-05-back.mp4"])

    def test_copy_makes_a_new_folder_with_every_file_and_never_reuses_one(self):
        paths = CF.resolve(["/footage/A/RecentClips/%s-%s.mp4" % (s, c) for s in ("2026-11-04_09-28-05", "2026-11-04_09-29-05") for c in ("front", "back")], self.fr, safe_join)
        dest = os.path.join(self.tmp.name, "out")
        os.makedirs(dest)
        cp = CF.Copier()

        def wait(job):      # a copy runs in a thread: it must be over before the folder is removed (a slow machine saw the folder still being written to)
            for _ in range(500):
                st = cp.status(job)
                if st["state"] != "running":
                    return st
                time.sleep(0.02)
            return st
        a = cp.start(paths, dest, "TeslaCam 09-28")
        st = wait(a["job"])
        self.assertEqual((st["state"], st["done"], st["n"], st["bytes_done"]), ("done", 4, 4, 4000))
        self.assertEqual(len(os.listdir(os.path.join(dest, "TeslaCam 09-28"))), 4)
        b = cp.start(paths, dest, "TeslaCam 09-28")
        self.assertTrue(b["dest"].endswith("TeslaCam 09-28 (2)"))
        self.assertEqual(cp.dest_of(a["job"]), os.path.join(dest, "TeslaCam 09-28"))
        self.assertEqual(wait(b["job"])["state"], "done")

    def test_copy_of_nothing_or_into_a_missing_folder_says_so(self):
        cp = CF.Copier()
        self.assertFalse(cp.start([], self.tmp.name, "x")["ok"])
        self.assertFalse(cp.start([os.path.join(self.root, "RecentClips", "2026-11-04_09-28-05-front.mp4")], "/no/such/dir", "x")["ok"])


if __name__ == "__main__":
    unittest.main()
