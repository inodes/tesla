#!/usr/bin/env python3
"""test_clip_icons_ui.py - the status icons on a TeslaCam thumbnail: left-aligned, no "Snapshots N to build" text, and an unfinished icon runs that step for that clip alone."""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "..", "Tools", "lib", "app_ui")
JS = open(os.path.join(UI, "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(UI, "app.css"), encoding="utf-8").read()


class ClipIcons(unittest.TestCase):
    def test_icons_sit_on_the_left_like_everything_else(self):
        self.assertIn(".evc>.kst{margin:0;flex-wrap:nowrap;align-items:center;min-height:22px;justify-content:flex-start}", CSS)
        self.assertNotIn(".srcr .kst", CSS)
        self.assertNotIn("margin:0 0 0 auto", CSS.split(".evc>.kst")[0].split(".srcr{")[-1])

    def test_badges_on_row_one_icons_on_row_two_on_every_tile(self):
        self.assertNotIn('${bd}${kpChips', JS)                   # the icons are no longer inside the badge row
        self.assertEqual(JS.count("<div class=\"srcr\">${bd}</div>${kpChips("), 2)       # both kinds of tile
        self.assertIn("min-height:22px", CSS.split(".srcr{")[1].split("}")[0])

    def test_a_drive_tile_with_only_saved_clips_still_says_drive(self):
        self.assertIn('${pk?"Parked":"Drive"}</span>`)+pill("saved","Saved")', JS)

    def test_no_snapshots_text_on_the_thumbnail(self):
        self.assertNotIn("to build</i>", JS)
        self.assertNotIn("Snapshots ${n0(r.snap_n)}", JS)

    def test_an_unfinished_icon_starts_that_step_for_that_clip(self):
        self.assertIn('data-kst="${k}"', JS)
        self.assertIn('k==="stills"?["stills","snapshots"]:k==="pics"?["pictures"]:[k]', JS)
        self.assertIn("runProc([{kind:it.kind,id:it.id}]", JS)
        self.assertIn("kstWire($(\"#tb\")", JS)             # the tiles
        self.assertIn("kstWire(el,()=>OPENK)", JS)           # the open clip's strip
        self.assertIn(".kc.ic.go{cursor:pointer}", CSS)

    def test_a_finished_icon_is_not_a_button(self):
        i = JS.index("const ic=(k,x,tip)=>")
        seg = JS[i:i + 700]
        self.assertIn('<i class="kc ic ok" title=', seg)
        self.assertEqual(seg.count("data-kst"), 1)


if __name__ == "__main__":
    unittest.main()
