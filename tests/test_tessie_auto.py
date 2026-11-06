#!/usr/bin/env python3
"""test_tessie_auto.py - when the app syncs Tessie by itself (lib/tessie_auto.py): on open, when stale, and again after an interruption.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_tessie_auto.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import tessie_auto as TA  # noqa: E402

GAP = 3600.0


class DueTest(unittest.TestCase):
    def test_never_synced_runs_at_once(self):
        self.assertTrue(TA.due(1000, None, 0, 0, GAP))

    def test_fresh_waits_and_stale_runs(self):
        self.assertFalse(TA.due(5000, 4000, 0, 0, GAP))
        self.assertTrue(TA.due(8000, 4000, 0, 0, GAP))

    def test_after_an_interruption_it_tries_again_soon_and_backs_off(self):
        self.assertFalse(TA.due(1100, 0, 1000, 1, GAP))          # 100 s after the first failure
        self.assertTrue(TA.due(1000 + 300, 0, 1000, 1, GAP))     # 5 minutes
        self.assertFalse(TA.due(1000 + 599, 0, 1000, 2, GAP))    # then 10
        self.assertTrue(TA.due(1000 + 600, 0, 1000, 2, GAP))
        self.assertTrue(TA.due(1000 + GAP, 0, 1000, 20, GAP))    # never longer than the gap

    def test_a_sync_the_person_stopped_is_left_alone_for_the_gap(self):
        self.assertFalse(TA.due(2000, None, 1000, 0, GAP, stopped=True))
        self.assertTrue(TA.due(1000 + GAP, None, 1000, 0, GAP, stopped=True))

    def test_zero_turns_it_off(self):
        self.assertFalse(TA.due(1e9, None, 0, 0, 0))


class OutcomeTest(unittest.TestCase):
    def test_the_statuses(self):
        self.assertEqual(TA.outcome(dict(running=True)), "running")
        self.assertEqual(TA.outcome(dict(running=False, ended=None)), "none")
        self.assertEqual(TA.outcome(dict(running=False, ended=5, rc=0)), "ok")
        self.assertEqual(TA.outcome(dict(running=False, ended=5, rc=1, lines=["x"])), "failed")
        self.assertEqual(TA.outcome(dict(running=False, ended=5, rc=-15, lines=["(stopped from the app)"])), "stopped")


if __name__ == "__main__":
    unittest.main()
