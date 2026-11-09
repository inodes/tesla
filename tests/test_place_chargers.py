"""A saved place is where the car was parked; if it was charged there the place lists its chargers (a Tesla Destination and a Chargefox can sit side by side) and each charge
records which one it used. A charger marked free means no invoice is expected."""
import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_charges as AC  # noqa: E402
import app_places  # noqa: E402
import charging_analyzer as CA  # noqa: E402

PLACE = {"lat": -33.8345, "lon": 151.0851, "radius_m": 250, "keywords": ["Rhodes"],
         "chargers": [{"name": "Rhodes Waterside", "network": "Chargefox", "free": True},
                      {"name": "Rhodes Waterside - Level P3", "network": "Tesla Destination", "rate_per_kwh": 0.2}]}


def analyzer(places, registries=None, choices=None, d=None):
    a = CA.ChargingAnalyzer.__new__(CA.ChargingAnalyzer)
    a.superchargers = {}
    a.personal_chargers = a.charging_stations = a.chargefox_chargers = a.evie_chargers = a.evx_chargers = a.bppulse_chargers = a.consolidated_stations = {}
    a.plugshare_chargers = registries or {}
    a.places = places
    a.charges_dirs = [d] if d else []
    a._chg_choices = choices
    return a


class Resolve(unittest.TestCase):
    def test_a_place_with_chargers_wins_over_a_registry_guess_and_defaults_to_its_first(self):
        ps = {"Rhodes Waterside": {"plugshare_metadata": {"network": "Chargefox"}, "location": {"lat": -33.835415, "lon": 151.085403, "radius_m": 150}}}
        a = analyzer({"Rhodes Shopping Centre": PLACE}, ps)
        name, net, _e, obj = a.resolve_location("1 Oulton Avenue", "", -33.83529, 151.0844)
        self.assertEqual((name, net), ("Rhodes Waterside", "Chargefox"))
        self.assertTrue(obj["chargers"])

    def test_a_supercharger_session_is_not_taken_by_a_place(self):
        a = analyzer({"Rhodes Shopping Centre": PLACE})
        name, net, _e, _o = a.resolve_location("x", "", -33.8345, 151.0851, is_supercharger=True)
        self.assertEqual(net, "Tesla Supercharger")

    def test_a_plain_saved_place_is_not_a_charger(self):
        plain = dict(PLACE)
        plain.pop("chargers")
        a = analyzer({"Rhodes Shopping Centre": plain})
        _n, net, _e, _o = a.resolve_location("x", "", -33.8345, 151.0851)
        self.assertNotIn(net, ("Chargefox", "Tesla Destination"))
        self.assertEqual(a._place_chargers(plain), [])

    def test_the_chosen_charger_per_charge_is_read_from_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "charge_chargers.json"), "w") as f:
                json.dump({"1793921522": "Rhodes Waterside - Level P3"}, f)
            a = analyzer({}, d=d)
            self.assertEqual(a._charger_choices(), {"1793921522": "Rhodes Waterside - Level P3"})

    def test_free_charger_expects_no_cost_and_a_priced_one_its_rate(self):
        a = analyzer({})
        dt = datetime.datetime(2026, 11, 7, 22, 12)
        free = a.get_expected_tariff_rate(dict(PLACE, charging=PLACE["chargers"][0]), dt)
        self.assertEqual((free["rate_per_kwh"], free["schedule_name"]), (0.0, "Free"))
        priced = a.get_expected_tariff_rate(dict(PLACE, charging=PLACE["chargers"][1]), dt)
        self.assertEqual(priced["rate_per_kwh"], 0.2)

    def test_default_flag_puts_that_charger_first(self):
        two = [{"name": "A"}, {"name": "B", "default": True}]
        self.assertEqual([c["name"] for c in CA.ChargingAnalyzer._place_chargers({"chargers": two})], ["B", "A"])


class SavePlace(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.pl = app_places.Places(self.d)
        with open(os.path.join(self.d, "places.json"), "w") as f:
            json.dump({"Rhodes Shopping Centre": {"lat": -33.8345, "lon": 151.0851, "radius_m": 250, "keywords": ["Rhodes"]}}, f)

    def places(self):
        with open(os.path.join(self.d, "places.json")) as f:
            return json.load(f)

    def test_chargers_are_saved_with_the_place_and_removed_when_empty(self):
        body = dict(old_name="Rhodes Shopping Centre", name="Rhodes Shopping Centre", lat=-33.8345, lon=151.0851, radius_m=250, keywords="Rhodes", notes="",
                    chargers=[dict(name="Rhodes Waterside", network="Chargefox", free=True, rate_per_kwh=""), dict(name="Level P3", network="Tesla Destination", free=False, rate_per_kwh="0.25")])
        self.assertTrue(self.pl.save(body)["ok"])
        chs = self.places()["Rhodes Shopping Centre"]["chargers"]
        self.assertEqual([(c["name"], c["free"], c.get("rate_per_kwh")) for c in chs], [("Rhodes Waterside", True, None), ("Level P3", False, 0.25)])
        self.assertTrue(self.pl.save(dict(body, chargers=[]))["ok"])
        self.assertNotIn("chargers", self.places()["Rhodes Shopping Centre"])

    def test_a_save_without_chargers_leaves_them_alone(self):
        self.pl.add_charger("Rhodes Shopping Centre", dict(name="W", network="Chargefox", free=True))
        body = dict(old_name="Rhodes Shopping Centre", name="Rhodes Shopping Centre", lat=-33.8345, lon=151.0851, radius_m=250, keywords="Rhodes", notes="x")
        self.assertTrue(self.pl.save(body)["ok"])
        self.assertEqual(len(self.places()["Rhodes Shopping Centre"]["chargers"]), 1)

    def test_bad_input_is_refused(self):
        for chs, word in (([dict(name="")], "name"), ([dict(name="a"), dict(name="A")], "both called"), ([dict(name="a", rate_per_kwh="x")], "number"), ([dict(name="a", rate_per_kwh="9")], "between")):
            r = self.pl.save(dict(old_name="Rhodes Shopping Centre", name="Rhodes Shopping Centre", lat=-33.8, lon=151.0, radius_m=100, chargers=chs))
            self.assertFalse(r["ok"])
            self.assertIn(word, r["error"])

    def test_add_charger_adds_or_updates_by_name_and_keeps_the_others(self):
        self.pl.add_charger("Rhodes Shopping Centre", dict(name="W", network="Chargefox", free=True))
        self.pl.add_charger("Rhodes Shopping Centre", dict(name="P3", network="Tesla Destination", free=False))
        self.pl.add_charger("Rhodes Shopping Centre", dict(name="w", network="Chargefox", free=False, rate_per_kwh=0.3))
        chs = self.places()["Rhodes Shopping Centre"]["chargers"]
        self.assertEqual([(c["name"], c["free"]) for c in chs], [("w", False), ("P3", False)])
        self.assertFalse(self.pl.add_charger("Nowhere", dict(name="x"))["ok"])


class Near(unittest.TestCase):
    def test_rows_merge_the_registries_and_carry_what_each_says(self):
        ps = {"Rhodes Waterside": {"plugshare_metadata": {"network": "Chargefox", "score": 5.7, "url": "https://www.plugshare.com/location/137100"},
                                   "location": {"lat": -33.835415, "lon": 151.085403, "radius_m": 150, "address": "Waterside/1 Rider Blvd"},
                                   "hardware": {"stalls": 5, "max_power_kw": 22.0, "connector_types": ["Type 2"]}, "compatibility": {"access": "Public"},
                                   "tariffs": {"per_kwh_flat": 0.0}}}
        a = analyzer({"Rhodes Shopping Centre": PLACE}, ps)
        a.chargefox_chargers = {"Rhodes Waterside": {"name": "Rhodes Waterside", "lat": -33.835294, "lng": 151.084431, "location": {"lat": -33.835294, "lon": 151.084431, "radius_m": 150}, "maxPower": "22.0", "chargeStations": [{}]}}
        s = dict(raw_charge=dict(latitude=-33.83529, longitude=151.0844), emoji="🅿️", is_supercharger=False)
        rows = AC.near_chargers(a, CA, s)
        self.assertEqual(len(rows), 1)                                   # PlugShare and Chargefox list the same charger: one row
        r = rows[0]
        self.assertEqual((r["name"], r["network"]), ("Rhodes Waterside", "Chargefox"))
        self.assertEqual(sorted(r["sources"]), ["chargefox", "plugshare"])
        self.assertEqual((r["info"]["stalls"] in (1, 5), r["info"]["kw"], r["info"]["access"], r["info"]["score"], r["info"]["ps_price"]), (True, 22.0, "Public", 5.7, 0.0))

    def test_home_superchargers_and_unpositioned_charges_get_none(self):
        a = analyzer({})
        self.assertEqual(AC.near_chargers(a, CA, dict(raw_charge=dict(latitude=-33.8, longitude=151.0), emoji="🏠")), [])
        self.assertEqual(AC.near_chargers(a, CA, dict(raw_charge=dict(latitude=-33.8, longitude=151.0), emoji="🔴", is_supercharger=True)), [])
        self.assertEqual(AC.near_chargers(a, CA, dict(raw_charge={}, emoji="🅿️")), [])


class Book(unittest.TestCase):
    def test_set_charger_writes_every_part_and_marks_the_book_out_of_date(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "charges"))
            b = AC.ChargeBook(d, d)
            b.book = dict(plugs=[dict(id="10", parts=[dict(id="10"), dict(id="20")])], sig=["x"])
            b.get = lambda: dict(book=b.book)
            self.assertTrue(b.set_charger("10", "Rhodes Waterside")["ok"])
            with open(os.path.join(d, "charges", "charge_chargers.json")) as f:
                self.assertEqual(json.load(f), {"10": "Rhodes Waterside", "20": "Rhodes Waterside"})
            self.assertTrue(b.dirty)
            self.assertTrue(b.set_charger("20", None)["ok"])
            with open(os.path.join(d, "charges", "charge_chargers.json")) as f:
                self.assertEqual(json.load(f), {})
            self.assertFalse(b.set_charger("nope", "x")["ok"])


if __name__ == "__main__":
    unittest.main()
