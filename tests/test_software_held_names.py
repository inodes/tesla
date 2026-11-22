"""software.sync.held_names lists the versions folder again only when it changed."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
from lib.software import sync as SY  # noqa: E402


class HeldNames(unittest.TestCase):
    def test_listed_again_only_when_the_folder_changed(self):
        d = tempfile.mkdtemp()
        for v in ("2026.38.5.5", "2026.44.1"):
            open(os.path.join(d, v + ".json"), "w").write("{}")
        old = time.time() - 30
        os.utime(d, (old, old))
        self.assertEqual(SY.held_names(d), ["2026.38.5.5", "2026.44.1"])
        real = os.listdir
        calls = []
        os.listdir = lambda p: (calls.append(p), real(p))[1]
        try:
            SY.held_names(d)
            self.assertEqual(calls, [])                             # the same folder: not listed
            open(os.path.join(d, "2026.44.2.json"), "w").write("{}")
            os.utime(d, (old + 5, old + 5))
            self.assertIn("2026.44.2", SY.held_names(d))
            self.assertEqual(len(calls), 1)
        finally:
            os.listdir = real


if __name__ == "__main__":
    unittest.main()


class IndexSignature(unittest.TestCase):
    """The index's signature is the held files' number and folder time: a file written as teslascope writes them (beside, renamed in) changes it."""
    def test_changes_when_a_file_is_replaced_not_otherwise(self):
        from lib.software import index as I
        d = tempfile.mkdtemp()
        ix = I.Index.__new__(I.Index)
        ix.data = d
        files = []
        for v in ("2026.38.5.5", "2026.44.1"):
            p = os.path.join(d, v + ".json")
            open(p, "w").write("{}")
            files.append(p)
        old = time.time() - 30
        os.utime(d, (old, old))
        a = ix._signature(files)
        self.assertEqual(ix._signature(files), a)
        open(files[0] + ".tmp", "w").write('{"x": 1}')
        os.replace(files[0] + ".tmp", files[0])
        self.assertNotEqual(ix._signature(files), a)
