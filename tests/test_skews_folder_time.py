"""clip_clock looks at the clips' own times again only when the provenance folder changed (its files are written beside and renamed in)."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402


class SkewsFolderTime(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.pd = os.path.join(self.d, "teslacam_provenance")
        os.makedirs(self.pd)
        self.write("2026-11-18.json")
        self.saved = (dict(CC._S), dict(CC._SK), CC._prov_files)
        CC._S["dir"] = self.d
        CC._SK.update(sig=None, at=0.0)
        CC._SK.pop("dirm", None)
        self.listed = []
        real = CC._prov_files
        CC._prov_files = lambda d: (self.listed.append(1), real(d))[1]

    def tearDown(self):
        CC._S.clear()
        CC._S.update(self.saved[0])
        CC._SK.clear()
        CC._SK.update(self.saved[1])
        CC._prov_files = self.saved[2]

    def write(self, name):
        p = os.path.join(self.pd, name)
        with open(p + ".part", "w") as f:
            f.write("{}")
        os.replace(p + ".part", p)
        old = time.time() - 30                                  # settled
        os.utime(self.pd, (old, old))

    def look(self):
        CC._SK["at"] = 0.0
        CC._skews_refresh()

    def test_files_looked_at_only_when_the_folder_changed(self):
        self.look()
        n = len(self.listed)
        self.assertGreater(n, 0)
        self.look()
        self.look()
        self.assertEqual(len(self.listed), n)                   # the folder is the same: its files are not looked at
        self.write("2026-11-19.json")
        os.utime(self.pd, (time.time() - 10, time.time() - 10))
        self.look()
        self.assertGreater(len(self.listed), n)


if __name__ == "__main__":
    unittest.main()
