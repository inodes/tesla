"""The version page's subtitle says "First seen ... in any region" and which region/car is shown, with the countries behind the region's count, so a filter name
("Europe, modely") is not read as where the version was seen."""
import os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
JS = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()


class VersionHeaderTest(unittest.TestCase):
    def test_header(self):
        i = JS.index("async function pageSoftwareOne")
        blk = JS[i:i + 2500]
        self.assertIn("in any region", blk)
        self.assertIn("Showing ${esc(SWS.region)}", blk)
        self.assertIn("swRegionCountries(u,d)", blk)
        self.assertNotIn('${esc(SWS.region)}${SWS.car?", "+esc((SWS.car.split("|")[0])):""}.</div>', blk)
        self.assertIn("function swRegionCountries", JS)


if __name__ == "__main__":
    unittest.main()
