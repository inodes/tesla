"""test_dark_thumb_ui.py - a card's picture is never a black frame: the page looks a few seconds on until the picture is lit (a camera that has only just woken up starts black).

    python3 -m unittest tests/test_dark_thumb_ui.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
JS = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()


class DarkThumb(unittest.TestCase):
    def test_a_kept_still_that_is_black_is_swapped_for_a_later_second(self):
        self.assertIn('onload="stLit(this)"', JS)
        self.assertIn("function stLit(img)", JS)
        self.assertIn("const frameDark=", JS)
        self.assertIn("const STSEC=[2,5,", JS)

    def test_a_video_frame_that_is_black_is_looked_past_too(self):
        i = JS.index("const vtLoad=")
        self.assertIn('addEventListener("seeked"', JS[i:i + 900])
        self.assertIn("frameDark(v)", JS[i:i + 900])


if __name__ == "__main__":
    unittest.main()
