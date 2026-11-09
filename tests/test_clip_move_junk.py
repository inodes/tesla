"""clip_move: a move copies data and time only (no ._ sidecar beside the copy), takes the source's ._ file with it, and the sweep removes the ._ / .DS_Store files."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_move as CM  # noqa: E402


class PutTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        self.src = os.path.join(self.t, "A", "x-front.mp4")
        self.dst = os.path.join(self.t, "B", "ev", "x-front.mp4")
        os.makedirs(os.path.dirname(self.src))
        with open(self.src, "wb") as f:
            f.write(b"v" * 100)
        with open(CM.sidecar(self.src), "wb") as f:
            f.write(b"apple")
        os.utime(self.src, (1_700_000_000, 1_700_000_000))

    def test_the_source_sidecar_goes_with_the_source_and_none_is_left_at_the_copy(self):
        self.assertEqual(CM.put(self.src, self.dst, None), "moved")
        self.assertEqual(os.listdir(os.path.dirname(self.dst)), ["x-front.mp4"])
        self.assertEqual(os.listdir(os.path.dirname(self.src)), [])
        self.assertEqual(int(os.stat(self.dst).st_mtime), 1_700_000_000)         # the time is kept

    def test_extended_attributes_are_not_copied(self):
        with mock.patch.object(shutil, "copy2", side_effect=AssertionError("copy2 copies the extended attributes")):
            self.assertEqual(CM.put(self.src, self.dst, None), "moved")

    def test_a_sidecar_the_copy_already_had_is_left(self):
        os.makedirs(os.path.dirname(self.dst))
        with open(CM.sidecar(self.dst), "wb") as f:
            f.write(b"old")
        CM.put(self.src, self.dst, "overwrite")
        self.assertTrue(os.path.exists(CM.sidecar(self.dst)))

    def test_delete_choice_removes_the_source_and_its_sidecar(self):
        os.makedirs(os.path.dirname(self.dst))
        with open(self.dst, "wb") as f:
            f.write(b"same")
        self.assertEqual(CM.put(self.src, self.dst, "delete"), "dropped")
        self.assertEqual(os.listdir(os.path.dirname(self.src)), [])


class SweepTests(unittest.TestCase):
    def test_removes_only_the_macos_files(self):
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        d = os.path.join(t, "SavedClips", "ev")
        os.makedirs(d)
        for n in ("a-front.mp4", "._a-front.mp4", ".DS_Store", "event.json", "._event.json"):
            with open(os.path.join(d, n), "wb") as f:
                f.write(b"x")
        n, done = CM.sweep_junk(os.path.join(t, "SavedClips"))
        self.assertEqual((n, done), (3, True))
        self.assertEqual(sorted(os.listdir(d)), ["a-front.mp4", "event.json"])


if __name__ == "__main__":
    unittest.main()
