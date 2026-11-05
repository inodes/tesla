"""Deleting what is parked at Home leaves the first and last minute of the park when a drive runs into or out of it (the drive's parking movements and pulling away are in them)."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import run_cuts as RC  # noqa: E402
import clip_keep as CK  # noqa: E402
import clips_purged as PG  # noqa: E402
from test_clip_keep import PurgeTest, S1, S2, S3, LIVE  # noqa: E402

BEFORE, AFTER = "2026-10-16_15-39-07", "2026-10-16_15-43-07"


class HomeEdgesTest(PurgeTest):
    def setUp(self):
        super().setUp()
        PG.set_file(os.path.join(self.data, "teslacam_purged.json"))          # a purge marks its minutes as gone: for this test's data folder only
        self.addCleanup(lambda: PG.set_file(None))
        f = os.path.join(self.data, "teslacam_ledger", "2026-10-16.json")
        day = json.load(open(f))
        for st in (S1, S2, S3):
            day["recent||%s-front" % st] = dict(size=10, drives=dict(J=dict(LIVE)))
            d = os.path.join(self.roots["J"], "RecentClips")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "%s-front.mp4" % st), "wb").write(b"x" * 10)
        json.dump(day, open(f, "w"))
        real = RC._S
        self.addCleanup(lambda: setattr(RC, "_S", real))
        self.addCleanup(lambda: setattr(RC, "_VER", RC._VER + 1))
        RC._S = {BEFORE: ["drive", None, None, False, CC.utc(BEFORE)],
                 S1: ["park", -33.8, 151.1, True, CC.utc(S3)],
                 AFTER: ["drive", None, None, True, CC.utc(AFTER)]}
        RC._VER += 1
        real_at = CK.IGN.at
        CK.IGN.at = lambda lat, lon: True
        self.addCleanup(lambda: setattr(CK.IGN, "at", real_at))

    def test_the_edges_of_a_park_that_a_drive_runs_into_are_left(self):
        self.assertEqual(RC.drive_edges(S1, S3), (True, True))

    def test_deleting_the_park_at_home_leaves_its_first_and_last_minute(self):
        k = self.keeper()
        r = k.status(force=True)["clips"]["recent"][S1]
        self.assertTrue(r["ignored"])
        out = k.purge([dict(kind="recent", id=S1)], ["video"], self.roots, confirmed=True)
        self.assertNotIn("error", out)
        self.wait(k)
        rd = os.path.join(self.roots["J"], "RecentClips")
        self.assertTrue(os.path.exists(os.path.join(rd, "%s-front.mp4" % S1)))
        self.assertFalse(os.path.exists(os.path.join(rd, "%s-front.mp4" % S2)))
        self.assertTrue(os.path.exists(os.path.join(rd, "%s-front.mp4" % S3)))

    def test_a_park_with_no_drive_known_beside_it_still_keeps_its_first_and_last_minute(self):
        RC._S = {S1: ["park", -33.8, 151.1, True, CC.utc(S3)]}
        RC._VER += 1
        k = self.keeper()
        k.status(force=True)
        k.purge([dict(kind="recent", id=S1)], ["video"], self.roots, confirmed=True)
        self.wait(k)
        rd = os.path.join(self.roots["J"], "RecentClips")
        self.assertTrue(os.path.exists(os.path.join(rd, "%s-front.mp4" % S1)))
        self.assertFalse(os.path.exists(os.path.join(rd, "%s-front.mp4" % S2)))
        self.assertTrue(os.path.exists(os.path.join(rd, "%s-front.mp4" % S3)))

    def test_delete_parked_at_home_in_one_go_keeps_the_drive_edges(self):
        k = self.keeper()
        k.status(force=True)
        self.assertEqual(k.home_clips(), [dict(kind="recent", id=S1)])
        self.assertIn("error", k.purge_home(self.roots, confirmed=False))
        out = k.purge_home(self.roots, confirmed=True)
        self.assertNotIn("error", out)
        self.wait(k)
        rd = os.path.join(self.roots["J"], "RecentClips")
        self.assertTrue(os.path.exists(os.path.join(rd, "%s-front.mp4" % S1)))
        self.assertFalse(os.path.exists(os.path.join(rd, "%s-front.mp4" % S2)))
        self.assertTrue(os.path.exists(os.path.join(rd, "%s-front.mp4" % S3)))

    def test_a_minute_in_which_the_car_moved_is_never_deleted_as_parked_at_home(self):
        S4 = "2026-10-16_15-43-07"
        f = os.path.join(self.data, "teslacam_ledger", "2026-10-16.json")
        day = json.load(open(f))
        day["recent||%s-front" % S4] = dict(size=10, drives=dict(J=dict(LIVE)))
        json.dump(day, open(f, "w"))
        rd = os.path.join(self.roots["J"], "RecentClips")
        open(os.path.join(rd, "%s-front.mp4" % S4), "wb").write(b"x" * 10)
        RC._S = {S1: ["park", -33.8, 151.1, True, CC.utc(S4)]}
        RC._VER += 1
        k = self.keeper()
        real = k.tc._tl("A").summary
        k.tc._tl("A").summary = lambda st: dict(vmax=25.0) if st == S2 else real(st)
        k.status(force=True)
        k.purge_home(self.roots, confirmed=True)
        self.wait(k)
        gone = [s for s in (S1, S2, S3, S4) if not os.path.exists(os.path.join(rd, "%s-front.mp4" % s))]
        self.assertEqual(gone, [S3])                      # the first and last minute are the drives', the second moved; only the one still minute between goes


if __name__ == "__main__":
    unittest.main()
