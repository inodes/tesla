"""oslog: the app's unified-log writer never raises, whatever the platform, and installs its hooks once."""
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import oslog  # noqa: E402


class OsLogTest(unittest.TestCase):
    def setUp(self):
        self._keep = dict(oslog._STATE)
        oslog._STATE.update(backend="none", libc=None, py=None)

    def tearDown(self):
        oslog._STATE.clear()
        oslog._STATE.update(self._keep)

    def test_logging_with_no_backend_does_nothing_and_does_not_raise(self):
        lg = oslog.get("perf")
        for f in (lg.debug, lg.info, lg.notice, lg.error, lg.fault):
            f("a line \x00 with a nul and unicode: café")
        self.assertIs(oslog.get("perf"), lg)

    def test_the_call_names_a_system_image_that_holds_the_format_and_is_first_made_by_a_child(self):
        src = open(os.path.join(HERE, "..", "Tools", "lib", "oslog.py"), encoding="utf-8").read()
        self.assertNotIn("c_void_p.in_dll", src)                                  # that read the header's first bytes as the pointer: it killed the app on 24 Nov
        self.assertIn("_find_format(", src)                                       # a format string outside every image logs as "<compose failure>"
        self.assertIn("_ctypes_safe()", src)                                      # the app never makes the raw call before a child process has

    def test_a_ctypes_child_that_fails_means_no_ctypes_backend(self):
        os.environ["TESELLATE_NO_OSLOG"] = "1"
        try:
            self.assertFalse(oslog._ctypes_safe())
        finally:
            del os.environ["TESELLATE_NO_OSLOG"]

    def test_fields_make_one_searchable_line(self):
        self.assertEqual(oslog.fields(path="/api/status", s="0.500"), "path=/api/status s=0.500")
        self.assertNotIn("\n", oslog.fields(msg="a\nb"))

    def test_a_broken_backend_is_swallowed(self):
        class Boom:
            def os_log_create(self, *a):
                raise OSError("no")
        oslog._STATE.update(backend="ctypes", libc=Boom())
        oslog.get("boom").error("still fine")

    def test_the_watchdog_notes_a_stall_and_a_health_line(self):
        seen = []
        orig = oslog.Logger.log
        oslog.Logger.log = lambda self, level, msg: seen.append((self.category, msg))
        try:
            hold = threading.Event()
            th = oslog.watchdog(interval=0.02, stall=0.05, beat=0.1, status=lambda: dict(keeper="idle"))
            import time
            time.sleep(0.4)
            cats = {c for c, _m in seen}
            self.assertIn("health", cats)
            self.assertTrue(any("keeper=idle" in m for c, m in seen if c == "health"))
            hold.set()
        finally:
            oslog.Logger.log = orig


if __name__ == "__main__":
    unittest.main()
