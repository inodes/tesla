import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
import clip_keep as CK  # noqa: E402
import drive_registry as REG  # noqa: E402
import drive_roles as DR  # noqa: E402

VID = "AAAA-1111"


class FolderDriveTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.vol = os.path.join(self.t, "LaCie1")                                   # an ordinary disk with a TeslaCam folder in a folder
        rec = os.path.join(self.vol, "jowua-rescue", "TeslaCam", "RecentClips")
        os.makedirs(rec)
        open(os.path.join(rec, "2026-10-22_20-55-23-front.mp4"), "w").close()
        self.cfg = os.path.join(self.t, "config.json")
        self._saved = (REG.config_path, REG.mounted_volumes, REG._mount_point, REG.volume_id, DR.PATTERNS, REG.drives.__defaults__)
        REG.config_path = lambda: self.cfg
        REG.mounted_volumes = lambda roots=("/Volumes",): [(self.vol, VID)]
        REG._mount_point = lambda p: self.vol
        REG.volume_id = lambda p: VID
        DR.PATTERNS = ()
        DR._RR[0] = 0

    def tearDown(self):
        REG.config_path, REG.mounted_volumes, REG._mount_point, REG.volume_id, DR.PATTERNS, _d = self._saved
        DR._RR[0] = 0
        shutil.rmtree(self.t, ignore_errors=True)

    def test_a_folder_on_a_disk_is_a_drive_found_by_the_disks_id(self):
        vid, rel = REG.folder_entry_for(os.path.join(self.vol, "jowua-rescue", "TeslaCam"))
        self.assertEqual((vid, rel), (VID, "jowua-rescue/TeslaCam"))
        e = REG.register(vid, "LaCie", "copy", size_gb=1, folder=rel)
        self.assertEqual((e["role"], e["folder"]), ("L", "jowua-rescue/TeslaCam"))
        ds = DR.details()
        self.assertEqual([(d["role"], d["label"], d["path"]) for d in ds], [("L", "LaCie", os.path.join(self.vol, "jowua-rescue", "TeslaCam"))])
        self.assertEqual(DR.find_root("L"), ds[0]["path"])
        self.assertEqual(DR.role_of(ds[0]["path"]), "L")
        self.assertIsNone(REG.lookup(VID))                                          # the disk itself is not a TeslaCam drive

    def test_it_is_read_after_the_three_drives_and_before_unidentified_ones(self):
        REG.register(VID, "LaCie", "copy", size_gb=1, folder="jowua-rescue/TeslaCam")
        DR._RR[0] = 0
        self.assertEqual(DR.read_roles(), "AJUL")
        self.assertTrue(DR.rank("A") < DR.rank("U") < DR.rank("L") < DR.rank("?"))

    def test_it_is_not_there_when_the_disk_is_away_and_forgetting_it_removes_it(self):
        REG.register(VID, "LaCie", "copy", size_gb=1, folder="jowua-rescue/TeslaCam")
        REG.mounted_volumes = lambda roots=("/Volumes",): []
        self.assertEqual(DR.details(), [])
        self.assertEqual(len(REG.drives()), 1)                                      # still registered
        self.assertTrue(REG.forget(VID, "jowua-rescue/TeslaCam"))
        self.assertEqual(REG.drives(), [])

    def test_a_folder_drive_is_deleted_from_and_moved_from_like_the_others(self):
        REG.register(VID, "LaCie", "copy", size_gb=1, folder="jowua-rescue/TeslaCam")
        DR._RR[0] = 0
        self.assertTrue(CK.writable("L"))
        self.assertTrue(CK.writable("J"))
        self.assertFalse(CK.writable("?"))
        for action in ("delete", "move"):
            plan, err = CK.ClipKeep.check(object(), action, "L", [], {"L": "/x", "A": "/a"})
            self.assertNotIn("only read", err or "")                                # it gets as far as asking what is selected


if __name__ == "__main__":
    unittest.main()
