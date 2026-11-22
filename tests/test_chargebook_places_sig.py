"""The charge book is rebuilt for a saved place only when it matters to a charge: a charger place, Home, or a place where a charge was (ChargeBook._places_sig)."""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
import app_charges as AC  # noqa: E402


class PlacesSigTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.b = AC.ChargeBook(os.path.join(HERE, "..", "Tools"), self.d)
        self.b.book = {"plugs": [{"lat": -33.8251, "lon": 151.0832}]}          # a charge at Rhodes
        self.places = {"Home": {"lat": -33.8066, "lon": 151.0796, "radius_m": 100, "type": "home", "charger": "Evnex"}}
        self.save()

    def save(self):
        with open(os.path.join(self.d, "places.json"), "w") as f:
            json.dump(self.places, f)
        t = time.time() + len(self.places)                                      # a new time for each save
        os.utime(os.path.join(self.d, "places.json"), (t, t))

    def test_a_place_with_no_charge_leaves_the_book(self):
        before = self.b._places_sig()
        self.places["Canada Bay Club, Five Dock, NSW"] = {"lat": -33.8665, "lon": 151.1216, "radius_m": 150}
        self.save()
        self.assertEqual(self.b._places_sig(), before)

    def test_a_place_where_a_charge_was_or_with_a_charger_rebuilds_it(self):
        before = self.b._places_sig()
        self.places["Rhodes Shopping Centre, Rhodes, NSW"] = {"lat": -33.8255, "lon": 151.0840, "radius_m": 200}
        self.save()
        mid = self.b._places_sig()
        self.assertNotEqual(mid, before)
        self.places["Gym, Ryde, NSW"] = {"lat": -33.81, "lon": 151.10, "radius_m": 100, "chargers": [{"name": "Gym", "free": True}]}
        self.save()
        self.assertNotEqual(self.b._places_sig(), mid)


if __name__ == "__main__":
    unittest.main()


class InvoiceSigTest(unittest.TestCase):
    """The book is built again for a new or changed invoice, not because a build rewrote invoice_locations.json beside them (it looped: every build changed the folder)."""
    def test_only_the_pdfs_count(self):
        d, inv = tempfile.mkdtemp(), tempfile.mkdtemp()
        b = AC.ChargeBook(os.path.join(HERE, "..", "Tools"), d)
        b.book = {"plugs": [], "invoice_dirs": [inv]}
        open(os.path.join(inv, "a.pdf"), "wb").write(b"%PDF a")
        before = b._inputs()
        time.sleep(0.01)
        with open(os.path.join(inv, "invoice_locations.json"), "w") as f:
            f.write("{}")
        t = time.time() + 5
        os.utime(inv, (t, t))
        self.assertEqual(b._inputs(), before)
        open(os.path.join(inv, "b.pdf"), "wb").write(b"%PDF b")
        self.assertNotEqual(b._inputs(), before)
