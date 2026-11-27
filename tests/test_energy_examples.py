"""The committed energy templates (Energy/*.example.json) load through the real loaders and show two examples each."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "Tools"))
sys.path.insert(0, os.path.join(ROOT, "Tools", "lib"))
from providers.power import power_agl, power_base  # noqa: E402


class EnergyExamples(unittest.TestCase):
    def test_provider_template_path_exists(self):
        path = power_agl.AGLProvider().tariff_rates_example_path() if hasattr(power_agl, "AGLProvider") else None
        self.assertTrue(path and os.path.isfile(os.path.join(ROOT, path)), path)

    def test_tariff_example_loads_with_two_periods(self):
        periods, plan = power_base.load_rate_periods_from_file(os.path.join(ROOT, "Energy", "agl_tariff_rates.example.json"), "AGL", "Energy/agl_tariff_rates.example.json")
        self.assertEqual(len(periods), 2)
        self.assertLess(periods[0]["_effective_from_date"], periods[1]["_effective_from_date"])
        with open(os.path.join(ROOT, "Energy", "agl_tariff_rates.example.json")) as f:
            self.assertEqual(json.load(f)["gst"], 0.1)

    def test_solar_example_has_two_panel_groups(self):
        with open(os.path.join(ROOT, "Energy", "solar_system.example.json")) as f:
            self.assertEqual(len(json.load(f)["panel_groups"]), 2)

    def test_no_provider_directory_at_the_repo_root(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "AGL", "agl_tariff_rates.example.json")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "Solar", "solar_system.example.json")))


if __name__ == "__main__":
    unittest.main()
