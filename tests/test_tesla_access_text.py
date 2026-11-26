"""A Tesla Destination charger page says who may use it ("Available for customers & guests 24/7. Self Park"): kept, and Tesla's open-to-other-EVs flag is read from the page itself."""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from providers.chargers import chargers_tesla as T  # noqa: E402

URL = "https://www.tesla.com/en_AU/findus/location/charger/dc33307"      # Rhodes Waterside - Level P3 (page as seen on 26/11/2026)


def page():
    with open(os.path.join(HERE, "fixtures", "tesla_findus_dc33307.json"), encoding="utf-8") as f:
        return json.load(f)


def parse(nd):
    ex = object.__new__(T.TeslaChargerExplorer)
    return ex._parse_scraped_data(URL, {}, nd, charger_type="destination_charger", list_title="Rhodes Waterside - Level P3")


class AccessTextTest(unittest.TestCase):
    def test_the_access_words_are_kept(self):
        _, rec = parse(page())
        self.assertEqual(rec["access"]["description"], "Available for customers & guests 24/7. Self Park")

    def test_no_access_block_means_an_empty_description(self):
        nd = page()
        del nd["props"]["pageProps"]["formattedData"]["chargerAccessTypeAndInstructions"]
        self.assertEqual(parse(nd)[1]["access"]["description"], "")

    def test_the_flag_in_the_page_decides_tesla_only(self):
        _, rec = parse(page())
        self.assertIs(rec["compatibility"]["tesla_only"], True)
        nd = copy.deepcopy(page())
        nd["props"]["pageProps"]["formattedData"]["chargerDetails"]["openToNonTeslas"] = True
        _, rec = parse(nd)
        self.assertIs(rec["compatibility"]["open_to_non_tesla"], True)
        self.assertIs(rec["compatibility"]["tesla_only"], False)


if __name__ == "__main__":
    unittest.main()
