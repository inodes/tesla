"""A drives sync that brought nothing new leaves drives_master.csv as it is (DriveAnalyzer.consolidate_drives): everything built from it is then not built again."""
import csv
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib.drives.analyzer import DriveAnalyzer  # noqa: E402

COLS = ["Started At (UTC)", "Started At TZ", "Started At (Local)", "Ended At (UTC)", "Ended At TZ", "Ended At (Local)", "Starting Location", "Ending Location",
        "Starting Latitude", "Starting Longitude", "Ending Latitude", "Ending Longitude", "Distance (km)"]


class DrivesMasterUnchanged(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dd = os.path.join(self.d, "drives")
        os.makedirs(self.dd)
        self.m = os.path.join(self.dd, "drives_master.csv")
        rows = [["1795300000", "Australia/Sydney", "2026-11-22 08:26 AEDT", "1795300600", "Australia/Sydney", "2026-11-22 08:36 AEDT", "Home", "Shops",
                 "-33.80", "151.07", "-33.81", "151.08", "5.0"],
                ["1795310000", "Australia/Sydney", "2026-11-22 11:13 AEDT", "1795310600", "Australia/Sydney", "2026-11-22 11:23 AEDT", "Shops", "Home",
                 "-33.81", "151.08", "-33.80", "151.07", "5.0"]]
        with open(self.m, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(COLS)
            w.writerows(rows)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_second_consolidation_does_not_write(self):
        a = DriveAnalyzer(tessie_dir=self.d)
        a.consolidate_drives(master_dir=self.dd)
        old = time.time() - 100
        os.utime(self.m, (old, old))
        before = os.stat(self.m).st_mtime_ns
        a.consolidate_drives(master_dir=self.dd)
        self.assertEqual(os.stat(self.m).st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
