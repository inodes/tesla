#!/usr/bin/env python3
"""test_moved_clip_playback.py - a clip that was moved to another drive plays from where it is now (the server looks on the other connected drives when the page names the old one), a clip that is on
its way says so (ClipKeep.moving), and a video that cannot be played goes black in its own tile with the reason on it, beside nothing. The Back link never points at a Sentry / moment redirect.

    python3 -m unittest tests/test_moved_clip_playback.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TESLA_PERF_LOG", "")
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import tesla_app  # noqa: E402


def js():
    with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
        return f.read()


class OtherDrive(unittest.TestCase):
    def test_a_clip_is_found_on_the_drive_it_was_moved_to(self):
        with tempfile.TemporaryDirectory() as t:
            roots = {r: os.path.join(t, r) for r in "AJLU"}
            rel = "SentryClips/ev1/2026-10-21_10-22-07-front.mp4"
            os.makedirs(os.path.dirname(os.path.join(roots["L"], rel)))
            with open(os.path.join(roots["L"], rel), "wb") as fh:
                fh.write(b"v")
            saved = (tesla_app.DR.read_roles, tesla_app.footage_root)
            tesla_app.DR.read_roles = lambda: ["A", "J", "U", "L"]
            tesla_app.footage_root = roots.get
            try:
                self.assertEqual(tesla_app.other_drive_file("U", rel), os.path.join(roots["L"], rel))      # the page still said U
                self.assertIsNone(tesla_app.other_drive_file("L", rel))                                    # nowhere else
                self.assertIsNone(tesla_app.other_drive_file("U", "SentryClips/ev1/none.mp4"))
            finally:
                tesla_app.DR.read_roles, tesla_app.footage_root = saved


class Page(unittest.TestCase):
    def test_a_video_that_cannot_play_goes_black_in_its_tile(self):
        s = js()
        self.assertIn("function lostVideo(v)", s)
        self.assertIn('fig.classList.add("lost","blk")', s)
        self.assertNotIn('box.insertAdjacentHTML("afterbegin",\'<div class="sub">This clip could not be played', s)      # nothing is added beside the tiles
        self.assertIn("/api/clips/moving?path=", s)
        self.assertIn("Being moved to ", s)

    def test_back_never_points_at_a_redirect(self):
        s = js()
        self.assertIn("NAV=NAV.filter((e,i,a)=>!(e&&NAVREDIR(e.h)&&a[i+1]&&NAVWIN(a[i+1].h)))", s)
        self.assertIn("if(p&&NAVREDIR(p.h)&&NAVWIN(location.hash||\"\"))", s)

    def test_the_label_follows_the_picture_it_sits_on(self):
        """Zoomed on one camera the label ended up far from the video: it is placed on a camera that is on screen and measured again whenever the picture is resized."""
        s = js()
        self.assertIn("new ResizeObserver(()=>{if(document.contains(fig))hudUpd()})", s)
        self.assertIn('vd.addEventListener("loadedmetadata",hudUpd)', s)
        self.assertIn("figs.find(shown)", s)


if __name__ == "__main__":
    unittest.main()
