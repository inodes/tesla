#!/usr/bin/env python3
"""test_event_cards_below_video_ui.py - the cards of a drive's Saved clips and Sentry events are filled in after the video: they sit under the video, map and telemetry
(not above the video, pushing it down), and a page opened on an event does not scroll to the video (it is already there). Checked as text.

    python3 -m unittest tests/test_event_cards_below_video_ui.py
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def read(*p):
    with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", *p), encoding="utf-8") as f:
        return f.read()


class EventCardsBelow(unittest.TestCase):
    def test_cards_have_their_own_rows_after_the_telemetry(self):
        css = read("app.css")
        row = lambda sel: int(re.search(r"#tdb\.side>" + re.escape(sel) + r"\{grid-row:(\d+)", css).group(1))
        self.assertGreater(row("#dev"), row(".dvc"))          # one column: straight under the video, before the map and the telemetry (not three screens down)
        self.assertLess(row("#dev"), row("#dmapc"))
        wide = lambda sel: int(re.search(r"#tdb\.side:not\(\.stack\)>" + re.escape(sel) + r"\{grid-row:(\d+)", css).group(1))
        self.assertGreater(wide("#dev"), wide("#dchc"))        # side by side: under the video, map and telemetry
        self.assertGreater(wide("#drv"), wide("#dchc"))

    def test_opening_on_an_event_does_not_scroll(self):
        js = read("app.js")
        i = js.index("const pickEv=")
        blk = js[i:i + 900]
        self.assertIn('if(!auto)$("#dvid").scrollIntoView', blk)


if __name__ == "__main__":
    unittest.main()
