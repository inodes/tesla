"""app_kept.Kept: a list kept by an earlier version of the program is shown at once, then built again."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import app_kept as AK  # noqa: E402


class KeptTest(unittest.TestCase):
    def test_an_earlier_versions_list_is_shown_at_once_and_replaced_behind(self):
        d = tempfile.mkdtemp()
        old = AK.Kept(d, "100.5")
        old.get("activity", lambda: ["old"], fresh=True)
        time.sleep(0.01)
        new = AK.Kept(d, "200.5", gap=0.0)
        built = []

        def build():
            built.append(1)
            return ["new"]
        raw, stale = new.get("activity", build)
        self.assertEqual(raw, b'["old"]')                         # no waiting for a build
        self.assertTrue(stale)
        for _ in range(100):
            if built:
                break
            time.sleep(0.02)
        time.sleep(0.1)
        self.assertEqual(new.get("activity", build)[0], b'["new"]')
        self.assertTrue(os.path.isfile(os.path.join(d, "api_activity_200.5.json")))

    def test_nothing_kept_builds_it(self):
        raw, stale = AK.Kept(tempfile.mkdtemp(), "1").get("x", lambda: [1])
        self.assertEqual((raw, stale), (b"[1]", False))


if __name__ == "__main__":
    unittest.main()
