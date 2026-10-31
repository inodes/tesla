"""app_data.Store._summaries: the drive summaries read in an earlier run are kept on this computer; only a drive folder changed since is read again."""
import builtins
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import app_data as AD  # noqa: E402


class SummariesKeptTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.folders = []
        for stamp in ("2026-10-30_17-00", "2026-10-30_18-00"):
            f = os.path.join(self.d, "drives", "deepdive", "2026-10-30", stamp + "_AEDT")
            os.makedirs(f)
            json.dump(dict(start="2026-10-30 %s:00" % stamp[-5:].replace("-", ":"), stamp=stamp), open(os.path.join(f, "drive_summary.json"), "w"))
            self.folders.append(f)
        AD._CACHE.clear()
        AD._SK.clear()

    def reads(self, store):
        real, n = builtins.open, [0]

        def counting(path, *a, **k):
            if str(path).endswith("drive_summary.json"):
                n[0] += 1
            return real(path, *a, **k)
        builtins.open = counting
        try:
            out = store._summaries()
        finally:
            builtins.open = real
        return out, n[0]

    def test_a_second_run_reads_only_what_changed(self):
        local = tempfile.mkdtemp()
        s1 = AD.Store(self.d)
        s1.local = local
        out, n = self.reads(s1)
        self.assertEqual((len(out), n), (2, 2))
        AD._CACHE.clear()
        AD._SK.clear()
        s2 = AD.Store(self.d)                                    # the app started again
        s2.local = local
        out, n = self.reads(s2)
        self.assertEqual((len(out), n), (2, 0))                  # nothing read from the data folder
        time.sleep(0.02)
        json.dump(dict(start="2026-10-30 18:00:00", stamp="x"), open(os.path.join(self.folders[1], "drive_summary.json.tmp"), "w"))
        os.replace(os.path.join(self.folders[1], "drive_summary.json.tmp"), os.path.join(self.folders[1], "drive_summary.json"))
        os.utime(self.folders[1], (time.time() + 5, time.time() + 5))
        AD._CACHE.clear()
        AD._SK.clear()
        out, n = self.reads(s2)
        self.assertEqual(n, 1)                                   # only the changed drive
        self.assertIn("x", [j.get("stamp") for j in out])


if __name__ == "__main__":
    unittest.main()
