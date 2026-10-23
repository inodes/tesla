"""A drive end that is not a saved place is shown as the street and the suburb: a street alone cannot be placed."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import app_data  # noqa: E402


class Short(unittest.TestCase):
    def test_street_and_suburb(self):
        self.assertEqual(app_data._short("205 Military Road, Dover Heights New South Wales 2030, Australia"), "205 Military Road, Dover Heights")
        self.assertEqual(app_data._short("9 B Rd, Ryde NSW 2112"), "9 B Rd, Ryde")

    def test_a_suburb_named_like_a_state_is_kept(self):
        self.assertEqual(app_data._short("1 A St, East Victoria Park Western Australia 6101, Australia"), "1 A St, East Victoria Park")

    def test_nothing(self):
        self.assertEqual(app_data._short(""), "")
        self.assertEqual(app_data._short("Somewhere"), "Somewhere")


if __name__ == "__main__":
    unittest.main()
