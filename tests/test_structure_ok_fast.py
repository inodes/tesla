"""drive_registry.structure_ok looks only as far as the first clip and keeps its answer while the clip folders are unchanged."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import drive_registry as REG  # noqa: E402


class StructureOkTest(unittest.TestCase):
    def setUp(self):
        self.tc = os.path.join(tempfile.mkdtemp(), "TeslaCam")
        os.makedirs(os.path.join(self.tc, "RecentClips"))
        for i in range(300):
            open(os.path.join(self.tc, "RecentClips", "2026-11-22_10-%02d-%02d-front.mp4" % (i // 60, i % 60)), "w").close()
        REG._SOK.clear()

    def test_a_car_shaped_folder_is_one(self):
        self.assertTrue(REG.structure_ok(self.tc))

    def test_kept_while_unchanged_and_looked_at_again_when_changed(self):
        calls = []
        real = REG._first_clip_ok
        REG._first_clip_ok = lambda tc, subs: (calls.append(1), real(tc, subs))[1]
        try:
            for _ in range(5):
                REG.structure_ok(self.tc)
            self.assertEqual(len(calls), 1)
            st = os.stat(os.path.join(self.tc, "RecentClips"))
            os.utime(os.path.join(self.tc, "RecentClips"), ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
            REG.structure_ok(self.tc)
            self.assertEqual(len(calls), 2)
        finally:
            REG._first_clip_ok = real

    def test_a_folder_of_other_files_is_not(self):
        os.makedirs(os.path.join(self.tc, "SavedClips", "x"))
        bad = os.path.join(tempfile.mkdtemp(), "TeslaCam")
        os.makedirs(os.path.join(bad, "RecentClips"))
        open(os.path.join(bad, "RecentClips", "holiday.mp4"), "w").close()
        self.assertFalse(REG.structure_ok(bad))
        self.assertFalse(REG.structure_ok(os.path.join(bad, "missing")))


    def test_a_folder_that_could_not_be_read_is_not_remembered_as_wrong(self):
        real = REG._first_clip_ok
        REG._first_clip_ok = lambda tc, subs: None            # one failed read (a drive waking up)
        try:
            self.assertFalse(REG.structure_ok(self.tc))
        finally:
            REG._first_clip_ok = real
        self.assertTrue(REG.structure_ok(self.tc))            # the folders are unchanged, and the answer was not kept

if __name__ == "__main__":
    unittest.main()
