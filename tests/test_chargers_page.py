"""Superchargers and chargers are their own page (side bar item Chargers), apart from Places (saved places, stops to review)."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "..", "Tools", "lib", "app_ui")


def read(n):
    with open(os.path.join(UI, n), encoding="utf-8") as f:
        return f.read()


class ChargersPage(unittest.TestCase):
    def test_the_side_bar_has_chargers_after_places(self):
        html = read("index.html")
        self.assertRegex(html, r'data-p="places">.*?Places</a>\s*<a href="#/chargers" data-p="chargers">.*?Chargers</a>')

    def test_the_routes_and_labels(self):
        js = read("app.js")
        self.assertIn('places:()=>pagePlaces("places"),chargers:()=>pagePlaces("chargers")', js)
        self.assertIn('chargers:"Chargers"', js)

    def test_each_page_has_its_own_tabs(self):
        js = read("app.js")
        m = re.search(r"const PLSEC=\{(.*?)\};", js)
        self.assertIsNotNone(m)
        places, chargers = m.group(1).split("chargers:")
        self.assertIn('"saved"', places)
        self.assertIn('"found"', places)
        self.assertNotIn('"sc"', places)
        self.assertIn('"sc"', chargers)
        self.assertIn('"chargers"', chargers)
        self.assertNotIn('"saved"', chargers)

    def test_a_supercharger_or_charger_page_lights_chargers(self):
        self.assertIn('place:/^[sc]:/.test(arg||"")?"chargers":"places"', read("app.js"))

    def test_new_place_is_only_on_places(self):
        js = read("app.js")
        self.assertIn('${sec==="places"?\'<button class="btn pri" id="add">+ New place</button>\':""}', js)


if __name__ == "__main__":
    unittest.main()


class MapLabels(unittest.TestCase):
    def test_tesla_chargers_are_prefixed_on_the_all_chargers_map_like_other_networks(self):
        js = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()
        self.assertIn('"Tesla":"Tesla Supercharger","Tesla Destination":"Tesla Destination Charger"', js)
        self.assertIn("name:mapName(r),lat:+r.lat", js)
