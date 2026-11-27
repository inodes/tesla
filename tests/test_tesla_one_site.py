"""Refreshing one Tesla site by name or id: the command, the script and the app's route."""
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "Tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "lib"))
sys.path.insert(0, os.path.join(TOOLS, "commands"))
import charger_find as CF  # noqa: E402


def read(*p):
    with open(os.path.join(TOOLS, *p), encoding="utf-8") as f:
        return f.read()


class Explorer:
    def load_active_registries(self):
        return {"Goulburn, NSW - Lockyer Street": {}}, {}

    def get_station_record(self, st, sc_reg=None, dc_reg=None):
        if st["slug"] == "31614":
            return "Goulburn, NSW - Lockyer Street", {}, "sc"
        return None, None, None


LIST = [{"title": "Goulburn 2, NSW", "live_title": "Goulburn 2, NSW", "slug": "31614", "url": "https://www.tesla.com/en_AU/findus/location/supercharger/31614"},
        {"title": "Goulburn, NSW", "live_title": "Goulburn, NSW", "slug": "goulburnsupercharger", "url": "https://www.tesla.com/en_AU/findus/location/supercharger/goulburnsupercharger"}]


class OneSite(unittest.TestCase):
    def test_found_by_registry_name_list_name_or_id(self):
        for name in ("Goulburn, NSW - Lockyer Street", "goulburn 2, nsw", "31614", " 31614 ", LIST[0]["url"]):
            self.assertEqual([h["slug"] for h in CF.tesla_site_matches(Explorer(), LIST, name)], ["31614"], name)

    def test_another_site_with_a_like_name_is_not_taken(self):
        self.assertEqual([h["slug"] for h in CF.tesla_site_matches(Explorer(), LIST, "Goulburn, NSW")], ["goulburnsupercharger"])
        self.assertEqual(CF.tesla_site_matches(Explorer(), LIST, "Goulburn"), [])
        self.assertEqual(CF.tesla_site_matches(Explorer(), LIST, ""), [])

    def test_script_and_app_route_take_a_site(self):
        sh = read("commands", "sync_charger_all.sh")
        self.assertIn('--site "$SITE"', sh)
        self.assertIn('ONLY="site"', sh)
        app = read("tesla_app.py")
        self.assertIn('SYNC.start(("--site", site))', app)
        self.assertIn('"--site"', read("commands", "charger_find.py"))


if __name__ == "__main__":
    unittest.main()
