"""An unsaved spot is opened as #/place/@new for every stop: its "around here" lookup must follow the position, not the key."""
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))

import app_places


class OsmKey(unittest.TestCase):
    def test_new_place_is_not_served_from_another_new_place(self):
        calls = []

        def query(lat, lon, radius):
            calls.append((lat, lon))
            return [dict(name="Cafe %.3f" % lat, group="Food", dist_m=10, lat=lat, lon=lon, tags={})]

        pm = types.SimpleNamespace(OVERPASS_ERROR="", query_overpass_pois=query)
        with tempfile.TemporaryDirectory() as d:
            pl = app_places.Places(d)
            saved = sys.modules.get("places_common")
            sys.modules["places_common"] = pm
            try:
                a = pl.osm("@new", -33.8, 151.1, 300)
                b = pl.osm("@new", -33.9, 151.0, 300)
                again = pl.osm("@new", -33.8, 151.1, 300)
            finally:
                if saved is None:
                    del sys.modules["places_common"]
                else:
                    sys.modules["places_common"] = saved
        self.assertEqual(a["items"][0]["name"], "Cafe -33.800")
        self.assertEqual(b["items"][0]["name"], "Cafe -33.900")
        self.assertTrue(again.get("cached"))
        self.assertEqual(len(calls), 2)


class SuburbState(unittest.TestCase):
    def test_suburb_and_state_of_a_spot(self):
        pm = types.SimpleNamespace(reverse_geocode=lambda la, lo: {"address": {"suburb": "Ermington", "state": "New South Wales", "ISO3166-2-lvl4": "AU-NSW"}})
        with tempfile.TemporaryDirectory() as d:
            pl = app_places.Places(d)
            saved = sys.modules.get("places_common")
            sys.modules["places_common"] = pm
            try:
                self.assertEqual(pl.suburb_state(-33.81392, 151.05474), ("Ermington", "NSW"))
            finally:
                if saved is None:
                    del sys.modules["places_common"]
                else:
                    sys.modules["places_common"] = saved


if __name__ == "__main__":
    unittest.main()
