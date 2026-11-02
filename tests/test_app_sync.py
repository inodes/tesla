"""test_app_sync.py - the app's script runner (Chargers and Drives syncs) runs the script it is given and reports its output.

    python3 -m unittest tests/test_app_sync.py
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_sync  # noqa: E402


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "fake_sync.sh"), "w") as f:
            f.write("echo one\necho two $TESLA_DATA_DIR\nexit 3\n")

    def wait(self, s):
        for _ in range(100):
            if not s.running():
                break
            time.sleep(0.05)
        time.sleep(0.1)

    def test_runs_the_named_script_and_keeps_its_output_and_exit_code(self):
        s = app_sync.ChargerSync(self.d, "/data", "fake_sync.sh")
        self.assertTrue(s.start()["ok"])
        self.wait(s)
        st = s.status(0)
        self.assertEqual((st["running"], st["rc"]), (False, 3))
        self.assertEqual(st["lines"], ["one", "two /data"])
        self.assertEqual(s.status(1)["lines"], ["two /data"])

    def test_a_missing_script_gives_an_error_that_does_not_name_it(self):
        # the error is shown in the app, and app text never names a script (docs/ui-guidelines.md section 1)
        r = app_sync.ChargerSync(self.d, "/data", "nope.sh").start()
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])
        self.assertNotIn("nope.sh", r["error"])

    def test_a_given_command_runs_instead_of_a_script(self):
        s = app_sync.ChargerSync(self.d, "/data", "tool.py", argv=[sys.executable, "-c", "import sys;print('args', len(sys.argv))"])
        self.assertTrue(s.start()["ok"])                      # the script file need not exist
        self.wait(s)
        st = s.status(0)
        self.assertEqual((st["rc"], st["lines"]), (0, ["args 1"]))


if __name__ == "__main__":
    unittest.main()
