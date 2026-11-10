#!/usr/bin/env python3
"""test_short_where_feed.py - the short-place index on the video label is fed again only when what it is fed from changed (the clips' own places, or
the minutes whose telemetry is read), not on every change of the learned clock.

    python3 -m unittest tests/test_short_where_feed.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_teslacam  # noqa: E402
import short_place  # noqa: E402

CC = app_teslacam.CC


class Store:
    def __init__(self):
        self.sum = {"2026-10-01_10-00-00": dict(p0=[-33.8, 151.0])}

    def summary(self, st):
        return self.sum.get(st)


class Feed(unittest.TestCase):
    def setUp(self):
        self.saved = (CC.where_token, CC.where_all, CC.where_parts, short_place.ShortPlace)
        self.where = {"2026-10-01_10-00-00": ("A St", "Ryde")}
        self.feeds = []
        test = self

        class SP:
            def __init__(self, *a, **k):
                pass

            def feed(self, items):
                test.feeds.append(list(items))

            def label(self, sub, lat, lon):
                return sub
        short_place.ShortPlace = SP
        CC.where_token = lambda: self.where
        CC.where_all = lambda: dict(self.where)
        CC.where_parts = lambda st: self.where.get(st, ("", ""))
        self.tc = app_teslacam.TeslaCam(tempfile.mkdtemp())
        self.store = Store()

    def tearDown(self):
        CC.where_token, CC.where_all, CC.where_parts, short_place.ShortPlace = self.saved

    def test_fed_once_while_nothing_it_uses_changes(self):
        for _ in range(3):
            self.assertEqual(self.tc._short_where(self.store, "2026-10-01_10-00-00"), "Ryde")
            CC._S["version"] += 1                           # the learned clock changed: nothing the index uses
        self.assertEqual(len(self.feeds), 1)

    def test_new_places_or_new_telemetry_feed_it_again(self):
        self.tc._short_where(self.store, "2026-10-01_10-00-00")
        self.where = dict(self.where, **{"2026-10-01_10-01-00": ("B St", "Epping")})
        self.store.sum["2026-10-01_10-01-00"] = dict(p0=[-33.7, 151.1])
        self.tc._short_where(self.store, "2026-10-01_10-00-00")
        self.assertEqual(len(self.feeds), 2)
        self.assertEqual(sorted(x[2] for x in self.feeds[-1]), ["Epping", "Ryde"])
        self.store.sum["2026-10-01_10-02-00"] = 0
        self.tc._short_where(self.store, "2026-10-01_10-00-00")
        self.assertEqual(len(self.feeds), 3)

    def test_a_minute_far_from_its_event_does_not_take_the_events_suburb(self):
        """A drive minute inside a Saved clip that was kept 10 minutes later in the CBD is where the car was (Drummoyne), not the event's suburb."""
        st_far, st_near, st_park = "2026-10-01_10-10-00", "2026-10-01_10-11-00", "2026-10-01_10-12-00"
        self.store.sum[st_far] = dict(p0=[-33.8600, 151.1550])               # about 5 km from the event
        self.store.sum[st_near] = dict(p0=[-33.8700, 151.2100])               # at the event
        self.tc.__dict__["_evp"] = (None, {st: (-33.8700, 151.2100, "Sydney CBD") for st in (st_far, st_near, st_park)})
        self.tc._event_places = lambda: self.tc.__dict__["_evp"][1]
        self.assertEqual(self.tc._short_where(self.store, st_far), "")
        self.assertEqual(self.tc._short_where(self.store, st_near), "Sydney CBD")
        self.assertEqual(self.tc._short_where(self.store, st_park), "Sydney CBD")      # no telemetry of its own: the event's place


if __name__ == "__main__":
    unittest.main()
