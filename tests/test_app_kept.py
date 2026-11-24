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
        self.assertFalse(os.path.exists(os.path.join(d, "api_activity_100.5.json")))      # the earlier version's copy is gone once this one has its own

    def test_after_an_edit_a_list_built_since_is_not_built_again(self):
        k = AK.Kept(tempfile.mkdtemp(), "1")
        built = []
        k.get("places", lambda: (built.append(1), ["a"])[1])
        edit = time.time()
        time.sleep(0.01)
        k.get("places", lambda: (built.append(1), ["b"])[1], fresh=edit)      # built after the edit
        for _ in range(5):                                                    # the page asks again and again for a minute after the edit
            self.assertEqual(k.get("places", lambda: (built.append(1), ["c"])[1], fresh=edit), (b'["b"]', False))
        self.assertEqual(len(built), 2)

    def test_a_slow_build_after_an_edit_gives_the_old_list_marked_stale(self):
        k = AK.Kept(tempfile.mkdtemp(), "1")
        k.FRESH_WAIT_S = 0.2
        k.get("activity", lambda: ["old"])
        edit = time.time()
        time.sleep(0.01)                                                      # the build below begins after the edit

        def slow():
            time.sleep(0.6)
            return ["new"]
        t0 = time.time()
        self.assertEqual(k.get("activity", slow, fresh=edit), (b'["old"]', True))     # not waited for: the page asks again
        self.assertLess(time.time() - t0, 0.5)
        time.sleep(0.7)
        self.assertEqual(k.get("activity", slow, fresh=edit), (b'["new"]', False))

    def test_nothing_kept_builds_it(self):
        raw, stale = AK.Kept(tempfile.mkdtemp(), "1").get("x", lambda: [1])
        self.assertEqual((raw, stale), (b"[1]", False))


    def test_an_answer_is_as_new_as_its_build_began(self):
        """A build that began before an edit and ended after it is not 'built since the edit'; nan and inf ask for nothing fresh."""
        k = AK.Kept(tempfile.mkdtemp(), "300.5")
        edit = []

        def build():
            edit.append(time.time())
            time.sleep(0.05)
            return ["x"]
        k.get("places", build, fresh=True)
        t_edit = edit[0] + 0.02                                  # made while that build was running
        self.assertLess(k.mem["places"][1], t_edit)
        for bad in (float("nan"), float("inf")):
            n = len(edit)
            k.get("places", build, fresh=bad)
            self.assertEqual(len(edit), n)

if __name__ == "__main__":
    unittest.main()
