"""app_timeline: intervals of Recent / Sentry / Saved per volume, the gaps in Recent, who else has the video of a gap, and the timeline of all volumes together.

    python3 -m unittest tests/test_app_timeline.py
"""
import calendar
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_timeline as T  # noqa: E402


def t(h, m, s=0, day=20):
    return calendar.timegm((2026, 11, day, h, m, s, 0, 0, 0))


def mins(h0, m0, n):
    return {t(h0, m0) + 60 * i for i in range(n)}


class Summary(unittest.TestCase):
    def test_minutes_that_follow_are_one_interval_and_a_missing_minute_is_a_gap(self):
        r = T.summarise({"A": {"recent": mins(10, 0, 5) | mins(10, 6, 3)}})
        a = r["roles"]["A"]
        self.assertEqual(a["recent"], [[t(10, 0), t(10, 5)], [t(10, 6), t(10, 9)]])
        self.assertEqual([(g["s"], g["e"]) for g in a["gaps"]], [(t(10, 5), t(10, 6))])
        self.assertEqual(a["minutes"], {"recent": 8, "sentry": 0, "saved": 0})

    def test_clock_jitter_between_minutes_is_not_a_gap(self):
        r = T.summarise({"A": {"recent": {t(10, 0), t(10, 1, 2), t(10, 2, 3)}}})
        self.assertEqual(r["roles"]["A"]["gaps"], [])
        self.assertEqual(len(r["roles"]["A"]["recent"]), 1)

    def test_a_gap_says_which_other_volume_has_the_video_and_how_much_is_covered_by_sentry_or_saved(self):
        r = T.summarise({"A": {"recent": mins(10, 0, 5) | mins(10, 20, 5), "sentry": mins(10, 5, 5)},
                         "L": {"recent": mins(10, 0, 30)}})
        g = r["roles"]["A"]["gaps"][0]
        self.assertEqual((g["s"], g["e"]), (t(10, 5), t(10, 20)))
        self.assertEqual(g["by"], ["L"])
        self.assertEqual(g["ss"], 0.33)                           # five of the fifteen minutes are Sentry
        self.assertEqual(r["roles"]["L"]["gaps"], [])

    def test_all_volumes_together_fill_each_others_gaps(self):
        r = T.summarise({"A": {"recent": mins(10, 0, 5) | mins(10, 20, 5)}, "L": {"recent": mins(10, 5, 15)}})
        self.assertEqual(r["all"]["recent"], [[t(10, 0), t(10, 25)]])
        self.assertEqual(r["all"]["gaps"], [])
        self.assertEqual(len(r["roles"]["A"]["gaps"]), 1)

    def test_a_gap_nobody_has_stays_in_the_consolidated_timeline(self):
        r = T.summarise({"A": {"recent": mins(10, 0, 5)}, "L": {"recent": mins(10, 30, 5)}})
        self.assertEqual([(g["s"], g["e"], g["ss"]) for g in r["all"]["gaps"]], [(t(10, 5), t(10, 30), 0.0)])
        self.assertEqual(r["t0"], t(10, 0))
        self.assertEqual(r["t1"], t(10, 35))

    def test_nothing_gives_an_empty_answer(self):
        r = T.summarise({})
        self.assertEqual((r["t0"], r["t1"], r["roles"], r["all"]["gaps"]), (None, None, {}, []))


class FromTheLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.root = os.path.join(self.d, "teslacam_ledger")
        os.makedirs(self.root)
        T._KEPT.clear()

    def write(self, day, entries):
        with open(os.path.join(self.root, day + ".json"), "w") as f:
            json.dump(entries, f)

    def test_a_file_that_is_gone_from_a_volume_is_not_on_its_timeline_and_all_cameras_are_one_minute(self):
        live = dict(first="2026-11-20T01:00:00Z", last="2026-11-20T02:00:00Z", gone=None)
        gone = dict(live, gone="2026-11-20T03:00:00Z")
        self.write("2026-11-20", {
            "recent||2026-11-20_10-00-00-front": dict(size=1, drives={"A": live, "J": gone}),
            "recent||2026-11-20_10-00-00-back": dict(size=1, drives={"A": live}),
            "recent||2026-11-20_10-01-01-front": dict(size=1, drives={"J": gone}),
            "sentry|2026-11-20_10-30-00|2026-11-20_10-29-00-front": dict(size=1, drives={"A": live}),
            "saved|2026-11-20_10-40-00|2026-11-20_10-39-00-front": dict(size=1, drives={"A": live}),
        })
        r = T.build(self.d)
        self.assertEqual(r["roles"]["A"]["minutes"], {"recent": 1, "sentry": 1, "saved": 1})
        self.assertEqual(r["roles"]["A"]["recent"], [[t(10, 0), t(10, 1)]])
        self.assertEqual(r["roles"]["A"]["sentry"], [[t(10, 29), t(10, 30)]])
        self.assertEqual(r["roles"]["A"]["saved"], [[t(10, 39), t(10, 40)]])
        self.assertNotIn("J", r["roles"])                         # nothing of it is left

    def test_the_answer_is_kept_until_a_file_of_the_log_changes(self):
        live = dict(first="x", last="x", gone=None)
        self.write("2026-11-20", {"recent||2026-11-20_10-00-00-front": dict(size=1, drives={"A": live})})
        a = T.build(self.d)
        self.assertIs(T.build(self.d), a)
        self.write("2026-11-21", {"recent||2026-11-21_10-00-00-front": dict(size=1, drives={"A": live})})
        self.assertIsNot(T.build(self.d), a)

    def test_no_log_gives_an_empty_answer(self):
        self.assertEqual(T.build(self.d)["roles"], {})


if __name__ == "__main__":
    unittest.main()
