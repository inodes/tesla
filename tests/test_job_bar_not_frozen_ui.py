#!/usr/bin/env python3
"""test_job_bar_not_frozen_ui.py - the TeslaCam job bar ("Building drive snapshots · 10 of 15") cannot freeze on a failed or slow answer: each ask of /api/clips/job gives up
after 8 s and is asked again, and only after many misses is the bar taken down (it used to stop asking for good at the first failure, leaving the last numbers on screen).

    python3 -m unittest tests/test_job_bar_not_frozen_ui.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class JobBar(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            js = f.read()
        i = js.index("const poll1=async()=>")
        self.poll = js[i:js.index("const loadKeep=", i)]

    def test_each_ask_has_a_time_limit(self):
        self.assertIn("AbortController", self.poll)
        self.assertIn("ac.abort()", self.poll)

    def test_a_failed_ask_is_asked_again_not_given_up_on(self):
        self.assertNotIn("catch(e){break}", self.poll)
        self.assertIn("++bad>=30", self.poll)

    def test_the_bar_is_taken_down_when_nothing_answers(self):
        self.assertIn("jobT=null;selbar()", self.poll)


if __name__ == "__main__":
    unittest.main()
