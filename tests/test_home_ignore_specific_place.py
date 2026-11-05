"""Two saved places whose radii both hold a point: the smaller one is the place (Home, 100 m, over the park 120 m away with 150 m)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import home_ignore as IGN  # noqa: E402

HOME = dict(name="Home", lat=-33.80000, lon=151.00000, radius=100)
PARK = dict(name="Lions Park", lat=-33.79970, lon=151.00130, radius=150)


class SpecificPlaceTest(unittest.TestCase):
    def setUp(self):
        IGN.set_places(lambda: [PARK, HOME])

    def test_a_point_in_both_is_the_smaller_place(self):
        # about 60 m from the park's centre and 70 m from Home's: the park is nearer, Home is more specific
        self.assertEqual(IGN.place_at(-33.79986, 151.00068), "Home")

    def test_a_point_only_in_the_park_is_the_park(self):
        self.assertEqual(IGN.place_at(-33.79970, 151.00158), "Lions Park")

    def test_home_is_ignored_where_it_is_the_place(self):
        self.assertTrue(IGN.at(-33.79986, 151.00068))


if __name__ == "__main__":
    unittest.main()
