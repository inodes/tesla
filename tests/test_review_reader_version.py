"""A minute whose signs were read by an older sign reader is read again; one read by today's reader is not."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib"))
import app_review as AR  # noqa: E402


class ReaderVersionTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        base = os.path.join(self.d, "drive_review")
        os.makedirs(os.path.join(base, "crops"))
        day = {"2026-11-01_10-00-00": {"n": 5, "d": [{"s": 1.0, "text": "60"}]},                      # tesseract era: no version stored
               "2026-11-01_10-01-00": {"n": 5, "d": [], "rv": AR.READER},                              # today's reader
               "2026-11-01_10-02-00": {"n": 5, "d": [{"s": 2.0, "text": "40"}], "rv": AR.READER - 1 if AR.READER > 1 else 0}}
        with open(os.path.join(base, "2026-11-01.json"), "w") as f:
            json.dump(day, f)
        open(os.path.join(base, "crops", "2026-11-01_10-00-00_00.jpg"), "wb").write(b"x")
        self.R = AR.Review(self.d)

    def test_fresh_leaves_out_the_older_readers_minutes(self):
        self.assertEqual(set(self.R.fresh()), {"2026-11-01_10-01-00"})
        self.assertEqual(len(self.R.scanned()), 3)          # what was found is still there to show

    def test_start_reads_the_stale_minutes_again(self):
        seen = []
        AR.requirements = lambda: dict(py=[], cmd=[])
        self.R._run = lambda todo: seen.extend(i[0] for i in todo)
        items = [(s, "/x/%s-front.mp4" % s, None) for s in ("2026-11-01_10-00-00", "2026-11-01_10-01-00", "2026-11-01_10-02-00")]
        self.R.start(items)
        self.R.t.join()
        self.assertEqual(sorted(seen), ["2026-11-01_10-00-00", "2026-11-01_10-02-00"])

    def test_a_new_record_carries_the_reader_version(self):
        import inspect
        self.assertIn("rv=READER", inspect.getsource(AR.scan_clip))


if __name__ == "__main__":
    unittest.main()
