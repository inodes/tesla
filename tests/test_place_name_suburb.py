"""A new saved place carries its suburb and state, and a note (Places.with_suburb, default_notes, save)."""
import json, os, sys, tempfile, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_places  # noqa: E402


def places(sub="Croydon Park", st="NSW", existing=None):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "places.json"), "w") as f:
        json.dump(existing or {}, f)
    pl = app_places.Places(d)
    pl.suburb_state = lambda la, lo: (sub, st)
    pl.read = lambda: json.load(open(os.path.join(d, "places.json")))
    return pl


def body(name, **kw):
    return dict(dict(old_name="", name=name, lat=-33.8952, lon=151.09738, radius_m=120, keywords="Rawson Street, Rawson St, Flower Power", notes=""), **kw)


class NameTest(unittest.TestCase):
    def test_suburb_and_state_are_appended(self):
        pl = places()
        self.assertEqual(pl.save(body("Flower Power"))["key"], "p:Flower Power, Croydon Park, NSW")
        self.assertIn("Flower Power, Croydon Park, NSW", pl.read())

    def test_a_name_that_already_ends_that_way_is_kept(self):
        pl = places()
        self.assertEqual(pl.save(body("Ampol, Ermington, NSW"))["key"], "p:Ampol, Ermington, NSW")

    def test_no_suburb_found_is_refused_with_how_to_fix_it(self):
        pl = places(sub="", st="")
        r = pl.save(body("Flower Power"))
        self.assertFalse(r["ok"])
        self.assertIn("Suburb", r["error"])
        self.assertEqual(pl.read(), {})

    def test_a_place_saved_earlier_keeps_its_name_when_edited(self):
        pl = places(existing={"Flower Power": {"lat": -33.8952, "lon": 151.09738, "radius_m": 120, "keywords": ["Rawson Street"], "notes": "x"}})
        r = pl.save(body("Flower Power", old_name="Flower Power", notes="y"))
        self.assertEqual(r["key"], "p:Flower Power")

    def test_renaming_a_place_adds_them_too(self):
        pl = places(existing={"Flower Power": {"lat": -33.8952, "lon": 151.09738, "radius_m": 120, "keywords": [], "notes": "x"}})
        self.assertEqual(pl.save(body("Flower Power Nursery", old_name="Flower Power"))["key"], "p:Flower Power Nursery, Croydon Park, NSW")

    def test_home_is_left_alone(self):
        pl = places(existing={"Home": {"lat": -33.8, "lon": 151.0, "radius_m": 100, "keywords": [], "notes": "", "home": True}})
        self.assertEqual(pl.save(body("Home", old_name="Home", notes="n"))["key"], "p:Home")


class BranchNameTest(unittest.TestCase):
    def name(self, n, sub, st="NSW", **kw):
        pl = places(sub=sub, st=st)
        return pl.with_suburb(n, -33.8, 151.0, **kw)[0]

    def test_a_branch_named_after_its_suburb_says_it_once(self):
        self.assertEqual(self.name("Anytime Fitness Carlingford", "Carlingford"), "Anytime Fitness, Carlingford, NSW")
        self.assertEqual(self.name("Tesla Service Parramatta", "Parramatta"), "Tesla Service, Parramatta, NSW")
        self.assertEqual(self.name("United Swimming Macquarie Park", "Macquarie Park"), "United Swimming, Macquarie Park, NSW")
        self.assertEqual(self.name("Airbnb Mooloolaba", "Mooloolaba", "QLD"), "Airbnb, Mooloolaba, QLD")

    def test_a_unique_name_that_holds_the_suburb_is_kept_whole(self):
        self.assertEqual(self.name("The Concord Club", "Concord"), "The Concord Club, Concord, NSW")
        self.assertEqual(self.name("West Ryde Coles", "West Ryde"), "West Ryde Coles, West Ryde, NSW")
        self.assertEqual(self.name("Top Ryde Shopping Centre", "Ryde"), "Top Ryde Shopping Centre, Ryde, NSW")

    def test_a_place_with_chargers_keeps_its_name(self):
        self.assertEqual(self.name("Rhodes Waterside Rhodes", "Rhodes", charger=True), "Rhodes Waterside Rhodes, Rhodes, NSW")

    def test_a_name_with_the_suburb_already_after_a_comma_gets_the_state_only(self):
        self.assertEqual(self.name("Anzac Park, West Ryde", "West Ryde"), "Anzac Park, West Ryde, NSW")

    def test_a_different_suburb_at_the_end_is_not_stripped(self):
        self.assertEqual(self.name("Anytime Fitness Newington", "Silverwater"), "Anytime Fitness Newington, Silverwater, NSW")


class NotesTest(unittest.TestCase):
    def test_blank_notes_get_name_and_street_like_the_command_line(self):
        pl = places()
        pl.save(body("Flower Power"))
        self.assertEqual(pl.read()["Flower Power, Croydon Park, NSW"]["notes"], "Flower Power (Rawson Street)")

    def test_typed_notes_are_kept(self):
        pl = places()
        pl.save(body("Flower Power", notes="Garden centre"))
        self.assertEqual(pl.read()["Flower Power, Croydon Park, NSW"]["notes"], "Garden centre")

    def test_no_keywords_the_name_alone(self):
        self.assertEqual(app_places.Places.default_notes("Hot Shott, Main Beach, QLD", []), "Hot Shott")


if __name__ == "__main__":
    unittest.main()
