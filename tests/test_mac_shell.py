"""mac_shell: the menu, the Dock badge text, and when the end of a job is told."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib"))
import mac_shell as MS  # noqa: E402


class ShellTest(unittest.TestCase):
    def test_the_menu_lists_every_page_of_the_side_bar_in_order(self):
        (title, items), = MS.menu_spec()
        self.assertEqual(title, "Go")
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "Tools", "lib", "app_ui", "index.html"), encoding="utf-8") as f:
            html = f.read()
        import re
        side = re.findall(r'<a href="#/(\w+)" data-p="\w+">', html)
        self.assertEqual([r for _, r in items], side)

    def test_badge_is_the_share_done_of_the_job_that_runs(self):
        self.assertEqual(MS.badge_text({"working": True, "done": 1, "total": 4}, None), "25%")
        self.assertEqual(MS.badge_text({"working": True, "done": 0, "total": 0}, None), "…")
        self.assertEqual(MS.badge_text({"working": False}, {"running": True, "step_done": 9, "step_total": 10}), "90%")
        self.assertEqual(MS.badge_text({"working": False}, {"running": False}), "")
        self.assertEqual(MS.badge_text({"working": True, "done": 7, "total": 5}, None), "100%")

    def test_a_job_that_ran_long_enough_is_told_once_when_it_ends(self):
        w = MS.Watcher(min_s=60)
        self.assertIsNone(w.tick("Making stills", True, 1000))
        self.assertIsNone(w.tick("Making stills", True, 1050))
        msg = w.tick("", False, 1130)
        self.assertEqual(msg, ("Tesellate", "Making stills finished (2 min)."))
        self.assertIsNone(w.tick("", False, 1140))                      # not told again

    def test_a_short_job_ends_quietly(self):
        w = MS.Watcher(min_s=60)
        w.tick("Making stills", True, 1000)
        self.assertIsNone(w.tick("", False, 1030))

    def test_the_notification_text_is_quoted_for_osascript(self):
        calls = []
        orig, plat = MS.subprocess.run, MS.sys.platform
        MS.subprocess.run = lambda cmd, **kw: calls.append(cmd)
        MS.sys.platform = "darwin"
        try:
            self.assertTrue(MS.notify('T"x', 'say "hi"\\'))
        finally:
            MS.subprocess.run, MS.sys.platform = orig, plat
        self.assertEqual(calls[0][:2], ["osascript", "-e"])
        self.assertIn('with title "T\\"x"', calls[0][2])
        self.assertIn('display notification "say \\"hi\\"\\\\"', calls[0][2])

    def test_nothing_happens_off_a_mac(self):
        orig = MS.sys.platform
        MS.sys.platform = "linux"
        try:
            self.assertFalse(MS.notify("a", "b"))
        finally:
            MS.sys.platform = orig


if __name__ == "__main__":
    unittest.main()
