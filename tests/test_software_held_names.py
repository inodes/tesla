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
