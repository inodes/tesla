#!/usr/bin/env python3
"""test_perf_log.py - the timing log (lib/perf_log.py): ps's elapsed time read right, only slow requests written, the file kept under 1 MB, and the page's
timings taken from POST /api/perf.

    python3 -m unittest tests/test_perf_log.py
"""
import http.client
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TESLA_PERF_LOG", "")
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import perf_log as PL  # noqa: E402


class Log(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = os.path.join(self.d, "Logs", "tesla_perf.log")
        self.env = os.environ.get("TESLA_PERF_LOG")
        os.environ["TESLA_PERF_LOG"] = self.f

    def tearDown(self):
        os.environ["TESLA_PERF_LOG"] = self.env if self.env is not None else ""
        shutil.rmtree(self.d)

    def lines(self):
        with open(self.f, encoding="utf-8") as f:
            return [l.rstrip("\n").split("\t") for l in f]

    def test_ps_elapsed_time(self):
        self.assertEqual(PL._etime("  00:07\n"), 7)
        self.assertEqual(PL._etime("01:02:03"), 3723)
        self.assertEqual(PL._etime("2-01:00:00"), 2 * 86400 + 3600)
        self.assertGreaterEqual(PL.since_launch(), 0)

    def test_marks_and_only_slow_requests(self):
        PL.mark("server bound", port=8765)
        PL.request("/api/fast", 0.05, 10)
        PL.request("/api/slow", 0.4, 12345)
        got = self.lines()
        self.assertEqual([l[1] for l in got], ["start", "slow"])
        self.assertIn("step=server bound", got[0])
        self.assertTrue(any(x.startswith("since_launch_s=") for x in got[0]))
        self.assertEqual(got[1][2:], ["path=/api/slow", "s=0.400", "bytes=12345"])

    def test_page_timings_keep_only_their_fields(self):
        PL.client({"route": "teslacam\tx", "ms": 312.6, "first_ms": 1800, "other": "no"})
        PL.client("not a dict")
        self.assertEqual(self.lines(), [self.lines()[0]])
        self.assertEqual(self.lines()[0][2:], ["route=teslacam x", "ms=312", "first_ms=1800"])

    def test_kept_under_a_megabyte(self):
        PL.MAX_BYTES, big = 2000, PL.MAX_BYTES
        try:
            for i in range(100):
                PL.request("/api/x%03d" % i, 1.0, i)
            self.assertLessEqual(os.path.getsize(self.f), 2000)
            self.assertTrue(os.path.exists(self.f + ".1"))
        finally:
            PL.MAX_BYTES = big

    def test_off_when_empty(self):
        os.environ["TESLA_PERF_LOG"] = ""
        PL.mark("x")
        self.assertFalse(os.path.exists(self.f))


class Post(Log):
    def test_the_page_posts_its_timings(self):
        import tesla_app
        srv = tesla_app.Server(("127.0.0.1", 0), tesla_app.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("POST", "/api/perf", body='{"route":"drives","ms":250,"first_ms":900}')
            r = c.getresponse()
            r.read()
            self.assertEqual(r.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(self.lines()[-1][1:], ["page", "route=drives", "ms=250", "first_ms=900"])


if __name__ == "__main__":
    unittest.main()
