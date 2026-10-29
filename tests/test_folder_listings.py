"""A folder that has not changed is not listed again by the footage check (teslacam_files.walk with stat=False), and the listings survive a restart."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))

import teslacam_files as TF


def touch(path, age=3600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def old(path, age=3600):
    t = os.path.getmtime(path) - age
    os.utime(path, (t, t))


class FolderListings(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.tc = os.path.join(self.d, "TeslaCam")
        for st in ("2026-10-11_16-12-14", "2026-10-11_16-13-14"):
            touch(os.path.join(self.tc, "RecentClips", st + "-front.mp4"))
        touch(os.path.join(self.tc, "SentryClips", "2026-10-11_16-20-00", "2026-10-11_16-12-14-front.mp4"))
        touch(os.path.join(self.tc, "SentryClips", "2026-10-11_16-20-00", "event.json"))
        for dp in (os.path.join(self.tc, "RecentClips"), os.path.join(self.tc, "SentryClips", "2026-10-11_16-20-00")):
            old(dp)
        TF._CACHE.clear()
        self.scans = []
        self._scandir = os.scandir
        os.scandir = lambda p: (self.scans.append(p), self._scandir(p))[1]

    def tearDown(self):
        os.scandir = self._scandir
        shutil.rmtree(self.d, ignore_errors=True)
        TF._CACHE.clear()

    def top(self):
        return sorted(os.path.basename(p) for p in self.scans)

    def keys(self):
        return sorted(TF.key(f) for f in TF.walk(self.tc, stat=False)[0])

    def test_same_files_as_the_full_walk(self):
        self.assertEqual(self.keys(), sorted(TF.key(f) for f in TF.walk(self.tc, stat=True)[0]))
        self.assertEqual(len(self.keys()), 3)

    def test_an_unchanged_folder_is_not_listed_again(self):
        self.keys()
        self.scans.clear()
        self.assertEqual(len(self.keys()), 3)
        self.assertEqual(self.top(), ["SavedClips", "SentryClips"])         # only the folders of events, to see which events there are

    def test_a_folder_that_gained_a_file_is_listed_again(self):
        self.keys()
        touch(os.path.join(self.tc, "RecentClips", "2026-10-11_16-14-14-front.mp4"))
        old(os.path.join(self.tc, "RecentClips"), 100)
        self.assertEqual(len(self.keys()), 4)

    def test_a_folder_changed_a_moment_ago_is_not_remembered(self):
        touch(os.path.join(self.tc, "RecentClips", "2026-10-11_16-14-14-front.mp4"))
        os.utime(os.path.join(self.tc, "RecentClips"), None)
        self.keys()
        self.assertNotIn(os.path.join(self.tc, "RecentClips"), TF._CACHE)

    def test_an_event_folder_that_is_gone_is_forgotten(self):
        self.keys()
        shutil.rmtree(os.path.join(self.tc, "SentryClips", "2026-10-11_16-20-00"))
        self.assertEqual(len(self.keys()), 2)
        self.assertFalse([k for k in TF._CACHE if "SentryClips" in k])

    def test_listings_are_kept_between_runs(self):
        self.keys()
        f = os.path.join(self.d, "cache", "folder_listings.json")
        TF.save_cache(f)
        TF._CACHE.clear()
        TF.load_cache(f)
        self.scans.clear()
        self.assertEqual(len(self.keys()), 3)
        self.assertEqual(self.top(), ["SavedClips", "SentryClips"])

    def test_sizes_come_from_the_kept_listing_once_the_folder_has_settled(self):
        rec = os.path.join(self.tc, "RecentClips")
        old(rec, 600)                                      # settled: more than two minutes ago
        full = {TF.key(f): (f["size"], f["mtime"]) for f in TF.walk(self.tc, stat=True)[0]}
        got = {TF.key(f): (f["size"], f["mtime"]) for f in TF.walk(self.tc, cached=True)[0]}
        self.assertEqual(got, full)
        open(os.path.join(rec, "2026-10-11_16-12-14-front.mp4"), "w").write("x" * 10)     # grows without the folder changing
        again = {TF.key(f): f["size"] for f in TF.walk(self.tc, cached=True)[0]}
        self.assertEqual(again["recent||2026-10-11_16-12-14-front"], 0)                  # the kept listing is used
        fresh = {TF.key(f): f["size"] for f in TF.walk(self.tc, stat=True)[0]}
        self.assertEqual(fresh["recent||2026-10-11_16-12-14-front"], 10)                 # a decision about a size still reads the drive

    def test_a_folder_changed_a_minute_ago_is_listed_with_sizes_every_time(self):
        rec = os.path.join(self.tc, "RecentClips")
        import time
        os.utime(rec, (time.time() - 30, time.time() - 30))
        TF.walk(self.tc, cached=True)
        self.assertNotIn("s|" + rec, TF._CACHE)

    def test_a_damaged_cache_file_is_ignored(self):
        f = os.path.join(self.d, "bad.json")
        open(f, "w").write("{nope")
        TF.load_cache(f)
        self.assertEqual(len(self.keys()), 3)


if __name__ == "__main__":
    unittest.main()
