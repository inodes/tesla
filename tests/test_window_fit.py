"""test_window_fit.py - the window comes back where it was and never where it cannot be reached (lib/window_place.py).

11 Nov 2026: the window was remembered on the second screen (x 2560); pywebview added the focused screen's own x at the next start and put it at 5120, off every screen,
at every restart.

    python3 -m unittest tests/test_window_fit.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import window_place as WP  # noqa: E402

MAIN = (0.0, 0.0, 2560.0, 1415.0)             # usable area: under the menu bar
SECOND = (2560.0, 0.0, 2560.0, 1415.0)        # to its right
LAPTOP = (0.0, 0.0, 1512.0, 944.0)
BOTH = [MAIN, SECOND]


class Fit(unittest.TestCase):
    def test_a_window_on_either_screen_stays_where_it_is(self):
        for f in ((100.0, 200.0, 1440.0, 940.0), (2560.0, 0.0, 2560.0, 1415.0), (2700.0, 300.0, 1800.0, 1000.0)):
            self.assertEqual(WP.fit(f, BOTH), f)

    def test_a_window_put_beyond_the_last_screen_comes_back_to_the_main_one(self):
        f = WP.fit((5120.0, 0.0, 2560.0, 1415.0), BOTH)                 # where pywebview put it
        self.assertTrue(WP.reachable(f, BOTH))
        self.assertEqual(f, (0.0, 0.0, 2560.0, 1415.0))

    def test_a_window_remembered_on_a_screen_that_is_gone_comes_to_the_one_left_and_fits_it(self):
        f = WP.fit((2700.0, 300.0, 1800.0, 1000.0), [LAPTOP])
        self.assertTrue(WP.reachable(f, [LAPTOP]))
        self.assertLessEqual(f[2], 1512)
        self.assertLessEqual(f[3], 944)
        self.assertGreaterEqual(f[0], 0)
        self.assertLessEqual(f[1] + f[3], 944 + 1)

    def test_a_title_bar_above_the_screen_or_under_the_menu_bar_is_pulled_down(self):
        f = WP.fit((300.0, 900.0, 1200.0, 800.0), [MAIN])               # top at 1700, above the 1415 there is
        self.assertEqual(f[1] + f[3], 1415.0)
        self.assertEqual((f[0], f[2], f[3]), (300.0, 1200.0, 800.0))    # only moved down

    def test_a_window_with_only_a_sliver_on_screen_is_pushed_in(self):
        f = WP.fit((-1350.0, 200.0, 1400.0, 900.0), [MAIN])             # 50 points of it showing at the left edge
        self.assertEqual(f[0], 0.0)
        g = WP.fit((2500.0, 200.0, 1400.0, 900.0), [MAIN])              # 60 points at the right edge
        self.assertEqual(g[0], 2560.0 - 1400.0)

    def test_a_window_whose_title_bar_shows_but_whose_bottom_hangs_off_is_left_alone(self):
        f = (400.0, -300.0, 1400.0, 900.0)
        self.assertEqual(WP.fit(f, [MAIN]), f)

    def test_a_window_below_every_screen_goes_to_the_middle_of_the_main_one(self):
        f = WP.fit((300.0, -5000.0, 1440.0, 940.0), BOTH)
        self.assertEqual(f, WP.centred((1440.0, 940.0), MAIN))

    def test_screens_at_different_heights_are_each_their_own(self):
        high = (2560.0, 600.0, 1920.0, 1055.0)                           # a smaller screen set higher up beside the main one
        on_it = (2700.0, 700.0, 1500.0, 900.0)
        self.assertEqual(WP.fit(on_it, [MAIN, high]), on_it)
        below_it = (2700.0, 0.0, 1500.0, 500.0)                          # beside the main screen's bottom, where the second screen does not reach
        f = WP.fit(below_it, [MAIN, high])
        self.assertTrue(WP.reachable(f, [MAIN, high]))

    def test_with_no_screens_known_nothing_is_moved(self):
        self.assertEqual(WP.fit((5120.0, 0.0, 800.0, 600.0), []), (5120.0, 0.0, 800.0, 600.0))

    def test_the_macs_window_is_found_in_either_shape_pywebview_gives(self):
        class NS:
            def setFrame_display_(self, r, d):
                pass
        ns = NS()
        self.assertIs(WP.nswindow(type("W", (), {"native": ns})()), ns)                          # pywebview 6: the window itself
        self.assertIs(WP.nswindow(type("W", (), {"native": type("B", (), {"window": ns})()})()), ns)      # older: wrapped
        self.assertIsNone(WP.nswindow(type("W", (), {"native": None})()))
        self.assertIsNone(WP.frame_of(type("W", (), {"native": None})()))


if __name__ == "__main__":
    unittest.main()
