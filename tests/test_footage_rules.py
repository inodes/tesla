"""test_footage_rules.py - the rules for footage: lib/teslacam_purge.py (only safe, old, wanted kinds are freed, oldest
first, never the newest days, and delete() removes only what was planned), lib/dashcam_clips.py (the clip list across
RecentClips / SavedClips / SentryClips, one clip per start time, and the prev+current+next telemetry timeline), and
lib/archive_naming.py (an archived file never overwrites another).

All files are empty or tiny made-up files in a temporary folder; no TeslaCam drive is read.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_footage_rules.py
"""
import datetime
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import teslacam_purge as TP  # noqa: E402
from lib import dashcam_clips as DC  # noqa: E402
from lib import archive_naming as AN  # noqa: E402


def minute(stamp, nbytes=100, safe=True, src="recent", paths=()):
    return dict(stamp=stamp, bytes=nbytes, safe=safe, src=src, paths=list(paths))


def report(detail, role="archive"):
    return {role: {"detail": detail}}


class PurgePlanTest(unittest.TestCase):
    DETAIL = [
        minute("2026-03-01_10-00"), minute("2026-03-01_10-01"), minute("2026-03-02_09-00", safe=False),
        minute("2026-03-03_08-00", src="saved"), minute("2026-03-05_12-00"),
        minute("2026-03-08_12-00"), minute("2026-03-09_12-00"), minute("2026-03-10_12-00"),
    ]

    def test_oldest_safe_recent_first_and_stops_when_enough(self):
        pl = TP.plan(report(self.DETAIL), "archive", need_bytes=150)
        self.assertEqual([d["stamp"] for d in pl["items"]], ["2026-03-01_10-00", "2026-03-01_10-01"])
        self.assertEqual((pl["minutes"], pl["bytes"], pl["enough"], pl["need"]), (2, 200, True, 150))
        self.assertEqual((pl["first"], pl["last"]), ("2026-03-01_10-00", "2026-03-01_10-01"))

    def test_never_the_newest_keep_days(self):
        # newest day 10 Mar, keep 3 days: 8, 9 and 10 Mar are never offered
        pl = TP.plan(report(self.DETAIL), "archive", need_bytes=10 ** 9, keep_days=3)
        self.assertEqual([d["stamp"][:10] for d in pl["items"]], ["2026-03-01", "2026-03-01", "2026-03-05"])
        self.assertFalse(pl["enough"])
        self.assertEqual(pl["available"], 300)
        # keep_days=0 offers every safe recent minute
        pl = TP.plan(report(self.DETAIL), "archive", need_bytes=10 ** 9, keep_days=0)
        self.assertEqual(pl["minutes"], 6)

    def test_unsafe_minutes_are_never_planned_but_are_counted(self):
        pl = TP.plan(report(self.DETAIL), "archive", need_bytes=10 ** 9)
        self.assertNotIn("2026-03-02_09-00", [d["stamp"] for d in pl["items"]])
        self.assertEqual(pl["skipped_unsafe_bytes"], 100)

    def test_saved_only_when_asked(self):
        pl = TP.plan(report(self.DETAIL), "archive", need_bytes=10 ** 9)
        self.assertNotIn("saved", {d["src"] for d in pl["items"]})
        pl = TP.plan(report(self.DETAIL), "archive", need_bytes=10 ** 9, kinds=("recent", "saved"))
        self.assertIn("2026-03-03_08-00", [d["stamp"] for d in pl["items"]])

    def test_nothing_needed_plans_nothing(self):
        for need in (0, -5):
            pl = TP.plan(report(self.DETAIL), "archive", need_bytes=need)
            self.assertEqual((pl["minutes"], pl["need"], pl["enough"], pl["first"]), (0, 0, True, None))

    def test_empty_drive(self):
        pl = TP.plan(report([]), "archive", need_bytes=100)
        self.assertEqual((pl["minutes"], pl["enough"]), (0, False))


class PurgeDeleteTest(unittest.TestCase):
    def test_removes_only_the_planned_files(self):
        with tempfile.TemporaryDirectory() as d:
            def f(name, size):
                p = os.path.join(d, name)
                with open(p, "wb") as fh:
                    fh.write(b"x" * size)
                return p
            a1, a2 = f("2026-03-01_10-00-00-front.mp4", 10), f("2026-03-01_10-00-00-back.mp4", 20)
            b1 = f("2026-03-01_10-01-00-front.mp4", 30)
            keep = f("2026-03-10_12-00-00-front.mp4", 40)
            gone = os.path.join(d, "already-gone.mp4")
            pl = {"items": [minute("2026-03-01_10-00", paths=[a1, a2, gone]), minute("2026-03-01_10-01", paths=[b1])]}
            seen = []
            n, freed, errs = TP.delete(pl, progress=lambda i, total, stamp: seen.append((i, total, stamp)))
            self.assertEqual((n, freed, errs), (3, 60, []))
            self.assertEqual(sorted(os.listdir(d)), [os.path.basename(keep)])
            self.assertEqual(seen, [(1, 2, "2026-03-01_10-00"), (2, 2, "2026-03-01_10-01")])

    def test_stop_before_the_next_minute(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(3):
                p = os.path.join(d, "m%d.mp4" % i)
                open(p, "wb").close()
                paths.append(p)
            pl = {"items": [minute("s%d" % i, paths=[p]) for i, p in enumerate(paths)]}
            calls = []
            n, _freed, _errs = TP.delete(pl, stop=lambda: calls.append(1) or len(calls) > 1)
            self.assertEqual(n, 1)
            self.assertEqual(len(os.listdir(d)), 2)


class FindClipsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def clip(self, rel, size=1):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * size)
        return p

    def test_one_timeline_across_the_three_folders(self):
        r1 = self.clip("RecentClips/2026-03-01_10-00-00-front.mp4")
        self.clip("RecentClips/2026-03-01_10-00-00-back.mp4")            # not the front camera
        self.clip("RecentClips/._2026-03-01_10-01-00-front.mp4")         # macOS metadata file
        self.clip("RecentClips/2026-02-28_23-59-00-front.mp4")           # before `since`
        s1 = self.clip("SavedClips/2026-03-01_10-05-00/2026-03-01_10-04-00-front.mp4")
        e1 = self.clip("SentryClips/2026-03-02_08-00-00/2026-03-02_07-59-00-front.mp4")
        self.clip("SentryClips/2026-02-01_08-00-00/2026-02-01_07-59-00-front.mp4")  # old event folder
        self.clip("RecentClips/2026-03-01_xx-front.mp4")                 # unreadable name
        got = DC.find_clips(self.root, "2026-03-01")
        self.assertEqual(got, [(datetime.datetime(2026, 3, 1, 10, 0), r1), (datetime.datetime(2026, 3, 1, 10, 4), s1),
                               (datetime.datetime(2026, 3, 2, 7, 59), e1)])

    def test_same_start_in_two_places_keeps_the_longer(self):
        self.clip("RecentClips/2026-03-01_10-00-00-front.mp4", size=10)      # cut short by a save
        full = self.clip("SavedClips/2026-03-01_10-01-00/2026-03-01_10-00-00-front.mp4", size=50)
        self.assertEqual(DC.find_clips([self.root], "2026-03-01"), [(datetime.datetime(2026, 3, 1, 10, 0), full)])

    def test_missing_folders_are_fine(self):
        self.assertEqual(DC.find_clips([os.path.join(self.root, "nope")], "2026-01-01"), [])

    def test_save_reasons_read_as_words(self):
        self.assertEqual(DC.REASONS["user_interaction_honk"], "you honked")
        self.assertTrue(all(isinstance(v, str) and v for v in DC.REASONS.values()))


def series(n, dur, v0):
    return dict(n=n, dur=dur, v=[v0 + i for i in range(n)], ap=[0] * n, br=[0] * n, lat=[0.0] * n, lon=[0.0] * n,
                hd=[0] * n, blk=[0] * n)


class FakeCache:
    def __init__(self, by_path):
        self.by_path = by_path

    def get(self, path):
        return self.by_path[path]


class TimelineTest(unittest.TestCase):
    T0 = datetime.datetime(2026, 3, 1, 10, 0)

    def make(self, starts_s, sizes=None):
        clips = [(self.T0 + datetime.timedelta(seconds=s), "c%d" % i) for i, s in enumerate(starts_s)]
        sizes = sizes or [6] * len(clips)
        cache = FakeCache({"c%d" % i: series(sizes[i], 60.0, 100 * i) for i in range(len(clips))})
        return clips, cache

    def test_prev_current_next_on_one_axis(self):
        clips, cache = self.make([-60, 0, 60])
        tl = DC.Timeline(clips, None, 1, cache)
        self.assertEqual([p for _dt, p, _S in tl.parts], ["c0", "c1", "c2"])
        self.assertEqual(tl.t[0], -60.0)
        self.assertEqual(tl.t[-1], 110.0)          # last sample of the next clip: 60 + 5 * 10 s
        self.assertEqual(tl.t, sorted(tl.t))
        self.assertTrue(tl.covers(0) and tl.covers(-60) and not tl.covers(111))
        self.assertEqual(tl.v[tl.idx(0)], 100)     # first sample of the current clip
        self.assertEqual(tl.idx(-1000), 0)
        self.assertEqual(tl.idx(1000), len(tl.t) - 1)
        self.assertEqual(tl.clip_at(30), ("c1", 30))
        self.assertEqual(tl.clip_at(-10), ("c0", 50))

    def test_neighbours_more_than_75_s_away_are_left_out(self):
        clips, cache = self.make([-120, 0, 80])
        tl = DC.Timeline(clips, None, 1, cache)
        self.assertEqual([p for _dt, p, _S in tl.parts], ["c1"])
        # a time past the end lands at the end of the nearest clip
        path, sec = tl.clip_at(500)
        self.assertEqual(path, "c1")
        self.assertAlmostEqual(sec, 59.9)

    def test_clip_without_telemetry_is_skipped(self):
        clips, cache = self.make([-60, 0], sizes=[0, 6])
        tl = DC.Timeline(clips, None, 1, cache)
        self.assertEqual([p for _dt, p, _S in tl.parts], ["c1"])


class ArchiveNamingTest(unittest.TestCase):
    def test_counter_prefix_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            first = AN.next_archive_path(d, "drives.csv")
            self.assertEqual(os.path.basename(first), "00_drives.csv")
            open(first, "w").close()
            second = AN.next_archive_path(d, "drives.csv")
            self.assertEqual(os.path.basename(second), "01_drives.csv")
            open(second, "w").close()
            self.assertEqual(os.path.basename(AN.next_archive_path(d, "drives.csv")), "02_drives.csv")
            # another name starts again at 00, and the extension stays at the end
            self.assertEqual(os.path.basename(AN.next_archive_path(d, "charges.csv")), "00_charges.csv")


if __name__ == "__main__":
    unittest.main()
