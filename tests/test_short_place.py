#!/usr/bin/env python3
"""test_short_place.py - the short place on the video label (lib/short_place.py): the clip's suburb, else the nearest clip's, else a lookup of the position; the state from the position, checked by lookup near a border.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_short_place.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import short_place as SP  # noqa: E402

HOME = (-33.8688, 151.2093)             # inside Sydney: far from a border


class LabelTest(unittest.TestCase):
    def mk(self, geo=None, **kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []

        def g(lat, lon):
            self.calls.append((lat, lon))
            return geo(lat, lon) if geo else None
        return SP.ShortPlace(self.tmp.name, geocode=g, background=False, **kw)

    def test_the_clips_own_suburb_is_used_with_the_state_of_the_position(self):
        sp = self.mk()
        self.assertEqual(sp.label("Main Beach", -27.98, 153.43), "Main Beach, QLD")
        self.assertEqual(self.calls, [])

    def test_a_clip_with_no_suburb_takes_the_nearest_clips_suburb(self):
        sp = self.mk()
        sp.feed([(HOME[0] + 0.001, HOME[1], "Surry Hills"), (HOME[0] + 0.5, HOME[1], "Far Away")])
        self.assertEqual(sp.label("", HOME[0], HOME[1]), "Surry Hills, NSW")           # the garage: about 110 m from a clip that knows
        self.assertEqual(self.calls, [])

    def test_a_far_neighbour_is_not_used_and_the_position_is_looked_up_once(self):
        sp = self.mk(geo=lambda a, b: {"address": {"suburb": "Redfern", "ISO3166-2-lvl4": "AU-NSW"}})
        sp.feed([(HOME[0] + 0.05, HOME[1], "Elsewhere")])
        self.assertEqual(sp.label("", HOME[0], HOME[1]), "Redfern, NSW")
        sp.label("", HOME[0], HOME[1])
        self.assertEqual(len(self.calls), 1)                                          # kept: not asked again

    def test_the_lookup_is_kept_in_a_file_for_the_next_start(self):
        sp = self.mk(geo=lambda a, b: {"address": {"suburb": "Redfern", "state": "New South Wales"}})
        sp.label("", HOME[0], HOME[1])
        again = SP.ShortPlace(self.tmp.name, geocode=lambda a, b: self.fail("asked again"), background=False)
        self.assertEqual(again.label("", HOME[0], HOME[1]), "Redfern, NSW")

    def test_no_answer_leaves_the_state_alone(self):
        sp = self.mk()
        self.assertEqual(sp.label("", HOME[0], HOME[1]), "NSW")

    def test_near_a_border_the_lookups_state_wins(self):
        sp = self.mk(geo=lambda a, b: {"address": {"suburb": "Coolangatta", "ISO3166-2-lvl4": "AU-QLD"}})
        self.assertEqual(sp.label("Coolangatta", -28.168, 153.54), "Coolangatta, QLD")      # the line says NSW or QLD to a few hundred metres: the lookup decides
        self.assertEqual(len(self.calls), 1)

    def test_no_position_gives_the_suburb_alone(self):
        self.assertEqual(self.mk().label("Tingalpa", None, None), "Tingalpa")


if __name__ == "__main__":
    unittest.main()
