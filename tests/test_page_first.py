"""page_first: a background build waits while another thread answers a request, never for its own."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import page_first as PF  # noqa: E402


class PageFirst(unittest.TestCase):
    def test_waits_while_another_thread_serves(self):
        go, done = threading.Event(), threading.Event()

        def request():
            with PF.serving():
                go.set()
                done.wait(2)
        t = threading.Thread(target=request)
        t.start()
        go.wait(2)
        self.assertTrue(PF.busy())
        PF._LAST[0] = 0
        t0 = time.monotonic()
        threading.Timer(0.2, done.set).start()
        PF.give_way(max_wait=2.0)
        waited = time.monotonic() - t0
        t.join(2)
        self.assertGreaterEqual(waited, 0.15)                 # it gave way until the request was answered
        self.assertLess(waited, 1.5)
        self.assertFalse(PF.busy())

    def test_never_waits_for_its_own_request(self):
        with PF.serving():
            self.assertFalse(PF.busy())
            PF._LAST[0] = 0
            t0 = time.monotonic()
            PF.give_way(max_wait=1.0)
            self.assertLess(time.monotonic() - t0, 0.05)

    def test_waits_at_most_max_wait(self):
        go, done = threading.Event(), threading.Event()

        def request():
            with PF.serving():
                go.set()
                done.wait(3)
        t = threading.Thread(target=request)
        t.start()
        go.wait(2)
        PF._LAST[0] = 0
        t0 = time.monotonic()
        PF.give_way(max_wait=0.2)
        self.assertLess(time.monotonic() - t0, 0.5)
        done.set()
        t.join(2)


if __name__ == "__main__":
    unittest.main()
