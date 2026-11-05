#!/usr/bin/env python3
"""test_timeline_days.py - the Drives timeline built day by day (TL.DayCache: only the days whose sources changed are worked out again) is the same as built
from nothing, on a made-up fixture: drives cut by gear, stops, unread minutes, a short hole joined, a drive over midnight, Saved and Sentry events.

    python3 -m unittest tests/test_timeline_days.py
"""
import copy
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import teslacam_timeline as TL  # noqa: E402

CAMS = ["front", "back", "left_repeater", "right_repeater"]
DAYS = ["2026-09-%02d" % d for d in range(1, 13)]
MIDNIGHT_DAY = DAYS[5]                      # a drive starts at 23:57 on this day and ends after midnight
QUIET_DAY = DAYS[8]                         # a day with no recording that runs into another day


def stamp(day, h, m, s=0):
    return "%s_%02d-%02d-%02d" % (day, h, m, s)


def series(gear, v, lat0, dlat):
    n = len(gear)
    return dict(n=n, dur=n, v=list(v), lat=[lat0 + dlat * i / n for i in range(n)], lon=[151.0 + dlat * i / n for i in range(n)], hd=[90] * n, ap=[1 if x > 50 else 0 for x in v],
                steer=[0] * n, accel=[10] * n, br=[0] * n, blk=[0] * n, gear=list(gear))


def drive_minute(lat0, speed=60.0, first=1, last=1):
    gear = [first] * 30 + [last] * 30
    v = [speed if g else 0.0 for g in gear]
    return series(gear, v, lat0, 0.01)


def park_minute(lat0):
    return series([0] * 60, [0.0] * 60, lat0, 0.0)


class Fake:
    """The made-up sources: Recent minutes, Saved and Sentry events and the telemetry readings (written into a real TL.Store folder)."""

    def __init__(self, base):
        self.base = base
        self.recent, self.saved, self.sentry = {}, [], []
        st = TL.Store(base)
        for k, day in enumerate(DAYS):
            lat = -33.0 - k * 0.1
            # a morning drive: Drive from Park, 6 minutes, into Park; then 3 parked minutes
            st_minutes = [stamp(day, 8, m) for m in range(0, 9)]
            for i, s in enumerate(st_minutes):
                self.recent[s] = list(CAMS)
                if i == 0:
                    st.put(s, drive_minute(lat, first=0, last=1))
                elif i < 5:
                    st.put(s, drive_minute(lat + 0.01 * i))
                elif i == 5:
                    st.put(s, drive_minute(lat + 0.05, first=1, last=0))
                else:
                    st.put(s, park_minute(lat + 0.06))
            # an afternoon drive with a 16 s hole (joined), and one minute not read yet
            for m, sec in ((14, 0), (15, 0), (16, 16), (17, 16)):
                s = stamp(day, 14 + (m - 14) // 60, m % 60, sec)
                self.recent[s] = list(CAMS)
                st.put(s, drive_minute(lat + 0.06 + 0.02 * (m - 14)))       # leaves from where the morning drive parked
            self.recent[stamp(day, 14, 18, 16)] = list(CAMS)            # not read yet: a recording
            # a Saved clip a while later and a Sentry event in the evening
            self.saved.append(dict(id="sv%d" % k, ts=stamp(day, 17, 30), clips={stamp(day, 17, 30): list(CAMS), stamp(day, 17, 31): list(CAMS)}))
            st.put(stamp(day, 17, 30), drive_minute(lat + 0.2))
            self.sentry.append(dict(id="se%d" % k, ts=stamp(day, 21, 5), clips={stamp(day, 21, 4): list(CAMS), stamp(day, 21, 5): list(CAMS)}, cam="front",
                                    reason="object", city="Town", street="Street", lat=lat, lon=151.0))
        # a drive over midnight
        nxt = DAYS[DAYS.index(MIDNIGHT_DAY) + 1]
        for i, s in enumerate([stamp(MIDNIGHT_DAY, 23, 57), stamp(MIDNIGHT_DAY, 23, 58), stamp(MIDNIGHT_DAY, 23, 59), stamp(nxt, 0, 0), stamp(nxt, 0, 1)]):
            self.recent[s] = list(CAMS)
            st.put(s, drive_minute(-34.0 + 0.01 * i, first=0 if i == 0 else 1, last=0 if i == 4 else 1))
        st.flush()

    def store(self):
        """A new store over the folder, as the app has after a start; counts the days whose readings it loads."""
        st = CountingStore(self.base)
        return st

    def build(self, store, cache=None):
        return TL.build(copy.deepcopy(self.recent), copy.deepcopy(self.saved), copy.deepcopy(self.sentry), store, cache=cache)


class CountingStore(TL.Store):
    def __init__(self, base):
        TL.Store.__init__(self, base)
        self.loaded = set()

    def series(self, st):
        if st[:10] not in self.days:
            self.loaded.add(st[:10])
        return TL.Store.series(self, st)


class Days(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.opts = dict(TL.OPTS)
        TL.OPTS["park_end_s"] = 10 ** 9          # these tests are about the day cache: the fixture's long park stays in the timeline (tests/test_timeline_end_sessions.py covers the cut)
        self.fx = Fake(os.path.join(self.tmp, "teslacam_timeline"))
        self.path = os.path.join(self.tmp, "cache", "timeline_days.json")

    def tearDown(self):
        TL.OPTS.clear()
        TL.OPTS.update(self.opts)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def no_gear(self, day):
        import json
        p = os.path.join(self.fx.base, day + ".json")
        with open(p) as f:
            d = json.load(f)
        for ser in d.values():
            ser.pop("gear", None)
        with open(p, "w") as f:
            json.dump(d, f)

    def same(self, cache, store=None):
        """Builds the old way (no cache) and the new way (with `cache`) from the same sources and checks they are equal; returns the new way's store."""
        old = self.fx.build(self.fx.store())
        st = store or self.fx.store()
        new = self.fx.build(st, cache)
        self.assertEqual(new, old)
        self.assertTrue(old)
        return st

    def test_fixture_has_every_kind(self):
        kinds = {x["kind"] for x in self.fx.build(self.fx.store())}
        self.assertTrue({"drive", "stopped", "recording", "sentry", "parked"} <= kinds | {"gap"}, kinds)
        over = [x for x in self.fx.build(self.fx.store()) if x["kind"] == "drive" and x["id"].startswith(MIDNIGHT_DAY + "_23-57")]
        self.assertEqual(len(over), 1)
        self.assertGreater(over[0]["t1"] - over[0]["t0"], 180)               # one drive over midnight

    def test_first_build_and_unchanged_rebuild(self):
        c = TL.DayCache(self.path)
        self.same(c)
        self.assertEqual(c.stats["rebuilt_days"], len(DAYS))
        st = self.same(c)
        self.assertEqual(c.stats["rebuilt_days"], 0)
        self.assertEqual(c.stats["rebuilt_runs"], 0)
        self.assertEqual(st.loaded, set())                                    # no day's readings were loaded

    def test_kept_file_is_used_after_a_start(self):
        self.same(TL.DayCache(self.path))
        c = TL.DayCache(self.path)                                            # a new start of the app: the cache comes from the file
        st = self.same(c)
        self.assertEqual(c.stats["rebuilt_days"], 0)
        self.assertEqual(st.loaded, set())

    def test_one_day_changed_rebuilds_that_day_only(self):
        c = TL.DayCache(self.path)
        self.same(c)
        day = DAYS[2]
        w = TL.Store(self.fx.base)
        w.put(stamp(day, 14, 18, 16), drive_minute(-33.5))                     # the unread minute is read now
        w.flush()
        st = self.same(c)
        self.assertEqual(c.stats["rebuilt_days"], 1)
        self.assertEqual(st.loaded, {day})
        st = self.same(c)                                                     # and then nothing again
        self.assertEqual(c.stats["rebuilt_days"], 0)

    def test_new_minutes_and_removed_minutes(self):
        c = TL.DayCache(self.path)
        self.same(c)
        self.fx.recent[stamp(DAYS[3], 19, 0)] = list(CAMS)                    # a new Recent minute (not read yet)
        self.same(c)
        self.assertEqual(c.stats["rebuilt_days"], 1)
        del self.fx.recent[stamp(DAYS[4], 8, 3)]                              # a minute gone from the log, in the middle of a drive
        self.same(c)
        self.assertEqual(c.stats["rebuilt_days"], 1)
        self.fx.recent = {k: v for k, v in self.fx.recent.items() if not k.startswith(DAYS[7])}       # a whole day gone
        self.fx.saved = [e for e in self.fx.saved if not e["ts"].startswith(DAYS[7])]
        self.same(c)

    def test_change_on_either_side_of_midnight(self):
        c = TL.DayCache(self.path)
        self.same(c)
        nxt = DAYS[DAYS.index(MIDNIGHT_DAY) + 1]
        w = TL.Store(self.fx.base)
        w.put(stamp(nxt, 0, 1), drive_minute(-33.96, first=1, last=1))       # no longer ends in Park: the drive runs on
        w.put(stamp(nxt, 0, 2), drive_minute(-33.95, first=1, last=0))
        w.flush()
        self.fx.recent[stamp(nxt, 0, 2)] = list(CAMS)
        self.same(c)
        w.put(stamp(MIDNIGHT_DAY, 23, 57), park_minute(-34.0))                 # the first minute is parked now
        w.flush()
        self.same(c)

    def test_a_join_over_a_short_hole_is_worked_out_again(self):
        c = TL.DayCache(self.path)
        self.same(c)
        before = self.fx.build(self.fx.store())
        day = DAYS[9]
        self.no_gear(day)                                                     # older readings carry no gear: a join is decided by speed and position
        self.same(c)
        before = self.fx.build(self.fx.store())
        w = TL.Store(self.fx.base)
        ser = drive_minute(-33.81017)                                         # now starts where the minute before the 16 s hole ended: one drive, not two
        ser["lon"] = [x + 0.00983 for x in ser["lon"]]
        w.put(stamp(day, 14, 16, 16), ser)
        w.flush()
        self.no_gear(day)
        self.same(c)
        after = self.fx.build(self.fx.store())
        n = lambda items: len([x for x in items if x["kind"] == "drive" and x["id"].startswith(day + "_14")])      # noqa: E731
        self.assertEqual((n(before), n(after)), (2, 1))

    def test_saved_and_sentry_changes(self):
        c = TL.DayCache(self.path)
        self.same(c)
        self.fx.saved[1]["clips"][stamp(DAYS[1], 17, 32)] = list(CAMS)
        self.fx.sentry[2]["clips"] = {}
        self.fx.sentry.append(dict(id="new", ts=stamp(QUIET_DAY, 3, 0), clips={stamp(QUIET_DAY, 3, 0): ["front"]}, lat=None, lon=None))
        self.same(c)

    def test_reading_not_yet_written_out(self):
        c = TL.DayCache(self.path)
        self.same(c)
        day = DAYS[6]
        st = self.fx.store()
        st.put(stamp(day, 14, 18, 16), drive_minute(-33.7))                   # read in this program, not flushed: the file says nothing changed
        old = self.fx.build(st)
        new = self.fx.build(st, c)
        self.assertEqual(new, old)
        self.assertEqual(c.stats["rebuilt_days"], 1)

    def test_thresholds_changed_start_again(self):
        c = TL.DayCache(self.path)
        self.same(c)
        TL.OPTS["run_gap_s"] = 30                                             # the 16 s hole no longer joins by time alone
        self.same(c)
        self.assertEqual(c.stats["rebuilt_days"], len(DAYS))
        TL.OPTS["join_max_s"] = 60
        self.same(c)

    def test_clock_change_rebuilds(self):
        c = TL.DayCache(self.path)
        self.same(c)
        real = TL.CC.utc
        try:
            TL.CC.utc = lambda st: real(st) + 3600                            # the car's clock learned an hour off
            self.same(c)
            self.assertEqual(c.stats["rebuilt_days"], len(DAYS))
        finally:
            TL.CC.utc = real

    def test_broken_cache_file(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        self.same(TL.DayCache(self.path))
        self.same(TL.DayCache(None))                                          # no file at all


if __name__ == "__main__":
    unittest.main()
