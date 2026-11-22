"""TeslaCam.segment finds a drive from the recordings around it (SEG_AROUND_S), not from everything on the drive: the same answer either way."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_teslacam as AT  # noqa: E402


def stamps(day, h, m, n):
    out = []
    for i in range(n):
        t = h * 60 + m + i
        out.append("%s_%02d-%02d-07" % (day, t // 60, t % 60))
    return out


class SegmentAround(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "TeslaCam")
        rc = os.path.join(self.root, "RecentClips")
        os.makedirs(rc)
        self.runs = [stamps("2026-10-14", 8, 0, 5), stamps("2026-10-16", 15, 40, 6), stamps("2026-10-16", 18, 0, 4), stamps("2026-10-19", 9, 10, 3)]
        for run in self.runs:
            for st in run:
                for c in ("front", "back"):
                    open(os.path.join(rc, "%s-%s.mp4" % (st, c)), "w").close()
        self.tc = AT.TeslaCam(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seg(self, sid, around):
        self.tc.SEG_AROUND_S = around
        d = self.tc.segment("A", self.root, sid, 0.0)
        return d and (d["item"]["t0"], d["item"]["t1"], [m["stamp"] for m in d["minutes"]])

    def test_the_same_drive_from_a_window_or_from_everything(self):
        for run in self.runs:
            for sid in (run[0], run[-1]):
                full = self.seg(sid, 1e9)
                self.assertTrue(full)
                self.assertEqual(self.seg(sid, 12 * 3600), full)
                self.assertEqual(self.seg(sid, 2 * 3600), full)            # a window so small the drive reaches its edge: found from everything

    def test_no_drive_at_a_time_with_no_footage(self):
        self.assertIsNone(self.seg("2026-10-15_03-00-07", 12 * 3600))


if __name__ == "__main__":
    unittest.main()
