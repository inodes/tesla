"""A volume's UUID is asked of diskutil once per mount, not on every look at the drives (drive_registry.volume_id)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import drive_registry as REG  # noqa: E402


class VolumeIdMemoTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.saved = (REG._read_volume_id, REG._mount_point, REG.VID_TTL_S)
        self.calls = []
        REG._read_volume_id = lambda mp: (self.calls.append(mp), "ABC-123")[1]
        REG._mount_point = lambda p: self.d
        REG._VID.clear()

    def tearDown(self):
        REG._read_volume_id, REG._mount_point, REG.VID_TTL_S = self.saved
        REG._VID.clear()

    def test_asked_once_while_the_same_volume_stays_mounted(self):
        self.assertEqual([REG.volume_id(self.d) for _ in range(5)], ["ABC-123"] * 5)
        self.assertEqual(len(self.calls), 1)

    def test_asked_again_after_a_while_and_when_there_was_no_answer(self):
        REG.VID_TTL_S = 0
        REG.volume_id(self.d)
        REG.volume_id(self.d)
        self.assertEqual(len(self.calls), 2)
        REG.VID_TTL_S = 60
        REG._VID.clear()
        REG._read_volume_id = lambda mp: (self.calls.append(mp), None)[1]
        REG.volume_id(self.d)
        REG.volume_id(self.d)
        self.assertEqual(len(self.calls), 4)                # no answer is not kept

    def test_a_folder_no_longer_there_has_none(self):
        REG.volume_id(self.d)
        REG._mount_point = lambda p: os.path.join(self.d, "gone")
        self.assertIsNone(REG.volume_id(self.d))


if __name__ == "__main__":
    unittest.main()
