"""footage_keeping: the Home+Recent preview sizes what the drives really hold (A10), and its purge only takes the tombstones it flagged (A6)."""
import os
import sys
import tempfile
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import lib.footage_keeping as F  # noqa: E402


def put(path, n):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * n)


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.a, self.j, self.u = (os.path.join(self.d, x, "TeslaCam") for x in ("A", "J", "U"))
        for r in (self.a, self.j):
            put(os.path.join(r, "RecentClips", "2026-10-01_08-00-00-front.mp4"), 100)
        os.makedirs(self.u)

    def test_size_counts_only_the_drives_holding_the_clips(self):
        b, n = F.size_on_all_drives([os.path.join(self.a, "RecentClips", "2026-10-01_08-00-00-front.mp4")], [self.a, self.j, self.u])
        self.assertEqual((b, n), (200, 2))                          # not 300 across 3 drives

    def test_package_keys(self):
        self.assertEqual(F.package_tombstone_key(dict(cat="Recent", dt=datetime(2026, 10, 1, 8, 0, 0))), "RecentClips/2026-10-01_08-00-00")
        self.assertEqual(F.package_tombstone_key(dict(cat="Sentry", is_folder=True, folder="/x/SentryClips/2026-10-01_09-00-00")), "SentryClips/2026-10-01_09-00-00")

    def test_a_scoped_scrub_leaves_other_tombstones(self):
        put(os.path.join(self.j, "SentryClips", "2026-09-01_10-00-00", "front.mp4"), 50)
        put(os.path.join(self.j, "SavedClips", "2026-09-02_10-00-00", "front.mp4"), 50)
        data = {"tombstones": {"SentryClips/2026-09-01_10-00-00": {"pending_drives": ["VOL_JOWUA"]},
                               "SavedClips/2026-09-02_10-00-00": {"pending_drives": ["VOL_JOWUA"]}}}
        saved = {}
        orig = (F.load_tombstones, F.save_tombstones, F.get_drive_identifier)
        F.load_tombstones = lambda m=None: data
        F.save_tombstones = lambda d, m=None: saved.update(d)
        F.get_drive_identifier = lambda p, m=None: "VOL_JOWUA"
        try:
            n, b, _r = F.scrub_pending_tombstones_for_drive(os.path.dirname(self.j), self.a, only={"SentryClips/2026-09-01_10-00-00"})
        finally:
            F.load_tombstones, F.save_tombstones, F.get_drive_identifier = orig
        self.assertEqual(n, 1)
        self.assertFalse(os.path.exists(os.path.join(self.j, "SentryClips", "2026-09-01_10-00-00", "front.mp4")))
        self.assertTrue(os.path.exists(os.path.join(self.j, "SavedClips", "2026-09-02_10-00-00", "front.mp4")))


if __name__ == "__main__":
    unittest.main()
