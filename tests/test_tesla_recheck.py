"""Tesla Refresh finds changes inside a site: the list only has names and positions, so sites not fetched lately are fetched again."""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "Tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "lib"))
from providers.chargers.chargers_tesla import TeslaChargerExplorer  # noqa: E402

URL = "https://www.tesla.com/en_AU/findus/location/supercharger/%s"


def entry(i, days_ago):
    t = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)).isoformat()
    return {"id": str(i), "tesla_metadata": {"findus_id": str(i), "findus_url": URL % i, "findus_list_name": "Site %s, NSW" % i, "name": "Tesla Supercharger - Site %s, NSW" % i},
            "location": {"lat": -33.0 - i, "lon": 151.0 + i}, "hardware": {"stalls": 20}, "last_verified": t}


def station(i):
    return {"title": "Site %s, NSW" % i, "live_title": "Site %s, NSW" % i, "url": URL % i, "slug": str(i), "type": "supercharger", "_lat": -33.0 - i, "_lon": 151.0 + i}


class Recheck(unittest.TestCase):
    def explorer(self):
        ex = TeslaChargerExplorer()
        reg = {"Site 1, NSW": entry(1, 0.2), "Site 2, NSW": entry(2, 3), "Site 3, NSW": entry(3, 200)}
        ex.load_active_registries = lambda: (reg, {})
        return ex

    def test_without_recheck_only_the_list_is_compared(self):
        plan = self.explorer().plan_sync([station(1), station(2), station(3)])
        self.assertEqual(plan["recheck"], [])

    def test_sites_not_fetched_within_the_days_are_rechecked(self):
        plan = self.explorer().plan_sync([station(1), station(2), station(3)], recheck_days=1)
        self.assertEqual([s["slug"] for s in plan["recheck"]], ["2", "3"])     # 1 was fetched 5 hours ago; 3 is stale (200 days) and is rechecked too

    def test_zero_days_rechecks_every_site(self):
        plan = self.explorer().plan_sync([station(1), station(2), station(3)], recheck_days=0)
        self.assertEqual(len(plan["recheck"]), 3)

    def test_a_new_site_is_new_not_a_recheck(self):
        plan = self.explorer().plan_sync([station(1), station(9)], recheck_days=0)
        self.assertEqual([s["slug"] for s in plan["new"]], ["9"])
        self.assertEqual([s["slug"] for s in plan["recheck"]], ["1"])


if __name__ == "__main__":
    unittest.main()
