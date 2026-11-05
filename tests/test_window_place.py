"""tesla_app.window_place / window_keep: the window opens at the size and place it was last left at."""
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import tesla_app as T  # noqa: E402


class WindowPlaceTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.real = T.local_cache_dir
        T.local_cache_dir = lambda: self.d
        T._WGEO.clear()

    def tearDown(self):
        T.local_cache_dir = self.real

    def test_first_time_the_configured_size(self):
        o = T.window_place(SimpleNamespace(screens=[]))
        self.assertNotIn("x", o)
        self.assertTrue(o["width"] > 0)

    def test_the_last_size_and_place_come_back(self):
        T.window_keep(w=1800, h=1100)
        T.window_keep(x=2700, y=80)
        time.sleep(1.3)
        T._WGEO.clear()
        scr = [SimpleNamespace(x=0, y=0, width=2560, height=1440), SimpleNamespace(x=2560, y=0, width=2560, height=1440)]
        o = T.window_place(SimpleNamespace(screens=scr))
        self.assertEqual((o["width"], o["height"], o["x"], o["y"]), (1800, 1100, 2700, 80))

    def test_a_place_on_a_display_no_longer_there_is_not_used(self):
        json.dump(dict(w=1800, h=1100, x=2700, y=80), open(os.path.join(self.d, "window.json"), "w"))
        o = T.window_place(SimpleNamespace(screens=[SimpleNamespace(x=0, y=0, width=2560, height=1440)]))
        self.assertEqual(o["width"], 1800)
        self.assertNotIn("x", o)

    def test_a_place_above_every_screen_is_not_used(self):
        # y is measured down from the top of the main screen: a negative y of this size puts the title bar above all screens (the Mac then gives the window no screen)
        json.dump(dict(w=1800, h=1100, x=100, y=-400), open(os.path.join(self.d, "window.json"), "w"))
        o = T.window_place(SimpleNamespace(screens=[SimpleNamespace(x=0, y=0, width=2560, height=1440)]))
        self.assertNotIn("x", o)

    def test_a_place_below_the_main_screen_is_not_used(self):
        json.dump(dict(w=1800, h=1100, x=100, y=1500), open(os.path.join(self.d, "window.json"), "w"))
        o = T.window_place(SimpleNamespace(screens=[SimpleNamespace(x=0, y=0, width=2560, height=1440)]))
        self.assertNotIn("x", o)

    def test_window_move_moves_a_remembered_place_and_survives_a_refusal(self):
        got = []
        T.window_move(SimpleNamespace(move=lambda x, y: got.append((x, y))), (100, 50))
        self.assertEqual(got, [(100, 50)])
        T.window_move(SimpleNamespace(move=lambda x, y: 1 / 0), (100, 50))
        T.window_move(SimpleNamespace(move=lambda x, y: got.append("no")), (None, None))
        self.assertEqual(got, [(100, 50)])

    def test_the_title_bar_height_is_taken_from_a_reported_size(self):
        T._CHROME[0] = None
        self.addCleanup(lambda: T._CHROME.__setitem__(0, None))
        frame = SimpleNamespace(size=SimpleNamespace(height=1128))
        win = SimpleNamespace(native=SimpleNamespace(window=SimpleNamespace(frame=lambda: frame, contentRectForFrameRect_=lambda f: SimpleNamespace(size=SimpleNamespace(height=1100)))))
        self.assertEqual(T.window_chrome(win), 28)
        self.assertEqual(T.window_chrome(SimpleNamespace()), 28)          # kept once found
        T._CHROME[0] = None
        self.assertEqual(T.window_chrome(SimpleNamespace()), 0)           # not a Mac window: nothing taken off


if __name__ == "__main__":
    unittest.main()
