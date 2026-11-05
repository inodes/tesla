#!/usr/bin/env python3
"""test_clip_clock_memo.py - clip_clock.utc keeps each stamp's answer, and forgets them all when what they come from changes: the learned runs,
the clips' own recording times, or the settings.

    python3 -m unittest tests/test_clip_clock_memo.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import clip_clock as CC  # noqa: E402

ST = "2026-10-11_14-45-00"


def plain(stamp):
    own = CC._SK["map"].get(stamp)
    if own is not None:
        return own
    n = CC.naive(stamp)
    return n - CC.car_offset(n)


class Memo(unittest.TestCase):
    def setUp(self):
        self.saved = (CC._S["runs"], CC._S["firsts"], CC._SK["map"])

    def tearDown(self):
        CC._S["runs"], CC._S["firsts"], CC._SK["map"] = self.saved
        CC._UTC.update(key=None, m={})

    def test_the_same_as_working_it_out(self):
        for st in (ST, "2026-04-05_02-30-00", "2026-01-01_00-00-00"):
            self.assertEqual(CC.utc(st), plain(st))
            self.assertEqual(CC.utc(st), plain(st))

    def test_new_runs_are_used_at_once(self):
        CC._set_runs([], bump=False)
        a = CC.utc(ST)
        n = CC.naive(ST)
        CC._set_runs([[n - 3600, n + 3600, 0.0]])            # the car's clock was on UTC then
        self.assertEqual(CC.utc(ST), n)
        self.assertNotEqual(a, n)

    def test_a_clips_own_time_wins_at_once(self):
        CC.utc(ST)
        CC._SK["map"] = dict(CC._SK["map"], **{ST: 123.0})
        self.assertEqual(CC.utc(ST), 123.0)
        CC._SK["map"] = {}
        self.assertEqual(CC.utc(ST), plain(ST))

    def test_changed_settings_forget_the_kept_answers(self):
        CC.utc(ST)
        key = CC._UTC["key"]
        real = CC.CFG.settings
        try:
            other = dict(real())
            CC.CFG.settings = lambda: other
            CC.utc(ST)
            self.assertIsNot(CC._UTC["key"][2], key[2])
        finally:
            CC.CFG.settings = real


if __name__ == "__main__":
    unittest.main()
