"""test_keeper_giveup.py - what the Keeper tried and could not do is not counted as still to do (so "All - N updates needed" ends), and a day's sign file is told from iCloud's copy of it.

    python3 -m unittest tests/test_keeper_giveup.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_review as AR  # noqa: E402
import event_shots as ES  # noqa: E402
import teslacam_stills as ST  # noqa: E402


class GiveUp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_a_minute_whose_video_gives_no_picture_is_marked_once_and_not_planned_again(self):
        stills = ST.Stills(self.d)
        stills.make = lambda stamp, video: 0
        clip = os.path.join(self.d, "a-front.mp4")
        with open(clip, "wb") as f:
            f.write(b"x" * 10)
        ST.make_many(stills, [dict(stamp="2026-10-01_10-00-00", role="", path=clip, mark_stamp=True)], workers=1, by="test")
        self.assertIn("stamp|2026-10-01_10-00-00", stills.unreadable())

    def test_a_camera_that_gave_no_picture_is_remembered_so_it_is_not_asked_for_again(self):
        shots = ES.EventShots(self.d)
        real = ST.run_ffmpeg
        ST.run_ffmpeg = lambda *a, **k: None                          # ffmpeg ran and cut nothing
        self.addCleanup(setattr, ST, "run_ffmpeg", real)
        made = shots.make("sentry", dict(id="ev1", ts="2026-10-01T10:00:00"), "2026-10-01_09-59-00", 30, {"front": "x.mp4"})
        self.assertEqual(made, [])
        self.assertEqual(shots.tried("sentry", "ev1"), {"front"})
        self.assertEqual(shots.have("sentry", "ev1"), set())


class DayFiles(unittest.TestCase):
    def test_the_copy_icloud_makes_of_a_days_file_does_not_count(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "drive_review"))
        old = {"2026-09-16_09-29-25": dict(n=60, d=[dict(s=36.0, text="60", c=90, side="L")])}              # read, never given the second look
        new = {"2026-09-16_09-29-25": dict(n=60, d=[dict(s=36.0, text="60", c=90, side="L")], rf=1)}
        for name, rec in (("2026-09-16.json", new), ("2026-09-16 2.json", old)):
            with open(os.path.join(d, "drive_review", name), "w") as f:
                json.dump(rec, f)
        self.assertEqual(AR.Review(d).unrefined(), set())
        self.assertEqual(len(AR.Review(d).scanned()), 1)


if __name__ == "__main__":
    unittest.main()
