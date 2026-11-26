"""teslacam_files: a folder whose modification time did not change but whose clips were deleted is listed again, not answered from the kept listing.

    python3 -m unittest tests/test_walk_cache_deletes.py
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import teslacam_files as TF  # noqa: E402


class DeletesWithoutANewTime(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.addCleanup(TF._CACHE.clear)
        TF._CACHE.clear()
        self.recent = os.path.join(self.d, "TeslaCam", "RecentClips")
        os.makedirs(self.recent)
        for m in range(40):
            for cam in ("front", "back"):
                with open(os.path.join(self.recent, "2026-11-20_10-%02d-00-%s.mp4" % (m, cam)), "wb") as f:
                    f.write(b"x" * 100)
        old = time.time() - 3600
        os.utime(self.recent, (old, old))                         # long enough ago for the listing to be kept

    def test_the_kept_listing_is_dropped_when_its_files_are_gone_though_the_folder_time_is_the_same(self):
        tc = os.path.join(self.d, "TeslaCam")
        files, ok = TF.walk(tc, cached=True)
        self.assertEqual((len(files), ok), (80, True))
        mt = os.stat(self.recent).st_mtime_ns
        keep = {"2026-11-20_10-39-00-front.mp4", "2026-11-20_10-39-00-back.mp4"}
        for n in os.listdir(self.recent):
            if n not in keep:
                os.remove(os.path.join(self.recent, n))
        os.utime(self.recent, ns=(mt, mt))                        # exFAT: the delete did not move the folder's time
        files, ok = TF.walk(tc, cached=True)
        self.assertEqual(sorted(f["stamp"] + "-" + f["cam"] for f in files), ["2026-11-20_10-39-00-back", "2026-11-20_10-39-00-front"])

    def test_an_unchanged_folder_is_still_answered_from_the_kept_listing(self):
        tc = os.path.join(self.d, "TeslaCam")
        TF.walk(tc, cached=True)
        real = os.scandir
        calls = []
        TF.os.scandir = lambda p: (calls.append(p), real(p))[1]
        self.addCleanup(setattr, TF.os, "scandir", real)
        TF.walk(tc, cached=True)
        self.assertNotIn(self.recent, calls)                      # not listed again


if __name__ == "__main__":
    unittest.main()
