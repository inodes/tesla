"""The place page shows the real address under the map (Places.address_of, own_suburb)."""
import os, sys, tempfile, unittest, json
HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_places  # noqa: E402
import places_common  # noqa: E402


class Empty:
    def drives(self): return []
    def charges(self): return []


class AddressTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.P = app_places.Places(self.d)
        self.calls = 0
        self._rg = places_common.reverse_geocode
        def rg(lat, lon):
            self.calls += 1
            return {"address": {"house_number": "31", "road": "George Street", "suburb": "Bexley", "ISO3166-2-lvl4": "AU-NSW", "postcode": "2207"}}
        places_common.reverse_geocode = rg

    def tearDown(self):
        places_common.reverse_geocode = self._rg

    def test_street_suburb_state_postcode_and_asked_once(self):
        self.assertEqual(self.P.address_of(Empty(), -33.93, 151.12), "31 George Street, Bexley, NSW 2207")
        self.P.address_of(Empty(), -33.93, 151.12)
        self.assertEqual(self.calls, 1)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "place_address_cache.json")))

    def test_the_suburb_the_place_is_saved_under_wins(self):
        self.assertEqual(self.P.own_suburb("31 George Street, Bexley, NSW 2207", "Mum, Bardwell Valley, NSW"), "31 George Street, Bardwell Valley, NSW 2207")
        self.assertEqual(self.P.own_suburb("31 George Street, Bexley, NSW 2207", "Home"), "31 George Street, Bexley, NSW 2207")

    def test_no_road_no_address(self):
        places_common.reverse_geocode = lambda a, b: {"address": {"suburb": "Bexley"}}
        self.assertEqual(self.P.address_of(Empty(), -33.0, 151.0), "")


if __name__ == "__main__":
    unittest.main()
