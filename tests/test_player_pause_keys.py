#!/usr/bin/env python3
"""test_player_pause_keys.py - the TeslaCam video player: Pause is the person's choice and outlives a new place, a new minute or a new clip;
Space pauses / plays; the arrow keys step by picture. The page's player code is checked as text (it needs a browser to run).

    python3 -m unittest tests/test_player_pause_keys.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js")


def player():
    with open(APP_JS, encoding="utf-8") as f:
        src = f.read()
    return src[src.index("let TCPAUSE=false;"):src.index("// The right-click menu of a video")]


class PlayerPauseKeysTest(unittest.TestCase):
    def setUp(self):
        self.p = player()

    def test_pause_outlives_the_player(self):
        self.assertTrue(self.p.startswith("let TCPAUSE=false;"))      # outside clipPlayer: a new clip's player reads it
        self.assertIn("if(!TCPAUSE)vs.forEach(x=>x.play()", self.p)      # a new minute / clip / place does not start playing when paused
        self.assertIn("mv.onended=()=>{if(TCPAUSE||", self.p)      # a seek to the end while paused must not run on
        self.assertIn("bplay=!TCPAUSE", self.p)      # a minute with no video honours it too

    def test_buttons_set_it(self):
        self.assertIn("if(mv.paused){TCPAUSE=false;", self.p)
        self.assertIn("else{TCPAUSE=true;vs.forEach(x=>x.pause())}", self.p)
        self.assertIn("bplay=!bplay;TCPAUSE=!bplay", self.p)

    def test_keys(self):
        self.assertIn('e.key===" "', self.p)
        self.assertIn('"ArrowLeft"', self.p)
        self.assertIn('"ArrowRight"', self.p)
        self.assertIn("kstep(e.key===\"ArrowRight\"?1:-1)", self.p)
        self.assertIn("window.__cpk", self.p)      # one key handler at a time

    def test_step_is_in_pictures_of_the_speed_chosen(self):
        self.assertIn("const n=Math.max(1,+rate)", self.p)
        self.assertIn("d*n", self.p)

    def test_no_double_toggle_from_a_focused_button(self):
        self.assertIn('if(tg==="BUTTON")return', self.p)
        self.assertIn("b.blur()", self.p)


if __name__ == "__main__":
    unittest.main()
