"""charge_paths.list_files finds the deep-dive files in both layouts (flat and day folders), the day layout winning, without a stat of each."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import charge_paths as CP  # noqa: E402


class ListFiles(unittest.TestCase):
    def setUp(self):
        self.r = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.r, ignore_errors=True)

    def touch(self, *parts):
        p = os.path.join(self.r, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
        return p

    def test_both_layouts_day_wins(self):
        flat = self.touch("charge_deepdive_2026-11-01_0800_AEDT_00.csv")
        dup_flat = self.touch("charge_deepdive_2026-11-02_0900_AEDT_00.csv")
        day = self.touch("2026-11-02", "s1", "charge_deepdive_2026-11-02_0900_AEDT_00.csv")
        other = self.touch("2026-11-03", "s2", "charge_deepdive_2026-11-03_1000_AEDT_00.json")
        self.touch("2026-11-03", "s2", "notes.txt")
        self.touch("notes.txt")
        os.makedirs(os.path.join(self.r, "charge_deepdive_folder"))           # a folder with the prefix is not a file
        got = dict(CP.list_files(self.r))
        self.assertEqual(set(got), {os.path.basename(flat), os.path.basename(day), os.path.basename(other)})
        self.assertEqual(got[os.path.basename(day)], day)
        self.assertNotEqual(got[os.path.basename(day)], dup_flat)

    def test_no_folder(self):
        self.assertEqual(CP.list_files(os.path.join(self.r, "missing")), [])


if __name__ == "__main__":
    unittest.main()
