"""School zone signs: the hours on the plate are read into 24-hour ranges (sign_classes.parse_times / find_school) and a Review minute carries them (app_review.Review.signs)."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import sign_classes as SC  # noqa: E402
import app_review as AR  # noqa: E402


class Hours(unittest.TestCase):
    def test_the_usual_ways_a_plate_is_written(self):
        want = ["08:00-09:30", "14:30-16:00"]
        for txt in ("8-9:30 2:30-4", "8.00 - 9.30AM 2.30 - 4.00PM", "8:00am-9:30am / 2:30pm-4:00pm", "8—9.30  2.30—4", "8-9:30AM 2:30-4PM"):
            self.assertEqual(SC.parse_times(txt), want, txt)

    def test_what_is_not_hours_gives_none(self):
        for txt in ("", "40", "SCHOOL ZONE", "8-8:10", "13-14", "99-100", "8-9:37"):
            self.assertEqual(SC.parse_times(txt), [], txt)

    def test_the_limit_beside_the_plate_is_not_an_hour(self):
        self.assertEqual(SC.parse_times("40 8-9:30"), ["08:00-09:30"])

    def test_school_is_told_from_other_boards_by_similar_letters(self):
        self.assertTrue(SC.is_school([("ScHooL", 50.0)]))
        self.assertTrue(SC.is_school([("ZONE", 80.0)]))
        self.assertFalse(SC.is_school([("EXIT", 90.0), ("NORTH", 80.0)]))


@unittest.skipUnless(shutil.which("tesseract"), "needs tesseract")
class Frame(unittest.TestCase):
    def sign(self, scale):
        import cv2
        import numpy as np
        im = np.full((700, 1000, 3), (110, 125, 120), np.uint8)
        x, y, bw, bh = 600, 250, int(150 * scale), int(75 * scale)
        lime = cv2.cvtColor(np.uint8([[[44, 210, 235]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        f = cv2.FONT_HERSHEY_DUPLEX
        th = max(1, int(2 * scale))
        cv2.rectangle(im, (x, y), (x + bw, y + bh), lime, -1)
        cv2.putText(im, "SCHOOL", (x + int(14 * scale), y + int(30 * scale)), f, 0.85 * scale, (0, 0, 0), th, cv2.LINE_AA)
        cv2.putText(im, "ZONE", (x + int(34 * scale), y + int(62 * scale)), f, 0.85 * scale, (0, 0, 0), th, cv2.LINE_AA)
        py, pw, ph = y + bh, int(100 * scale), int(75 * scale)
        cv2.rectangle(im, (x, py), (x + pw, py + ph), (235, 235, 235), -1)
        cv2.putText(im, "8-9:30", (x + int(6 * scale), py + int(28 * scale)), f, 0.7 * scale, (0, 0, 0), th, cv2.LINE_AA)
        cv2.putText(im, "2:30-4", (x + int(6 * scale), py + int(62 * scale)), f, 0.7 * scale, (0, 0, 0), th, cv2.LINE_AA)
        cx, cy, r = x + pw + int(35 * scale), py + int(37 * scale), int(33 * scale)
        cv2.circle(im, (cx, cy), r, (235, 235, 235), -1)
        cv2.circle(im, (cx, cy), r, (30, 30, 210), max(2, int(7 * scale)))
        cv2.putText(im, "40", (cx - int(20 * scale), cy + int(12 * scale)), f, 1.0 * scale, (0, 0, 0), th, cv2.LINE_AA)
        return im

    def test_a_near_sign_gives_its_board_and_its_hours(self):
        r = SC.find_school(self.sign(1.0))
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["text"], r[0]["side"], r[0]["times"]), ("School zone", "R", ["08:00-09:30", "14:30-16:00"]))
        self.assertGreater(r[0]["crop"].shape[0], r[0]["h"])                  # the picture kept shows the plate under the board

    def test_a_far_sign_is_still_a_school_zone_but_no_hours_are_made_up(self):
        r = SC.find_school(self.sign(0.5))
        self.assertTrue(all(x["times"] == [] for x in r))

    def test_a_yellow_green_patch_with_no_letters_is_not_read_at_all(self):
        import cv2
        import numpy as np
        im = np.full((700, 1000, 3), (110, 125, 120), np.uint8)
        lime = cv2.cvtColor(np.uint8([[[44, 210, 235]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        cv2.rectangle(im, (600, 250), (750, 325), lime, -1)
        self.assertEqual(len(SC._sz_boards(im)), 1)                          # it looks like a board ...
        self.assertLess(SC._letter_blobs(im, SC._sz_boards(im)[0][:4]), SC.SZ_MIN_BLOBS)
        real = SC._tsv
        SC._tsv = lambda *a, **k: self.fail("tesseract was started for a patch with no letters")
        try:
            self.assertEqual(SC.find_school(im), [])
        finally:
            SC._tsv = real
        b = SC._sz_boards(self.sign(1.0))[0][:4]
        self.assertGreaterEqual(SC._letter_blobs(self.sign(1.0), b), SC.SZ_MIN_BLOBS)    # ... a real board has its letters

    def test_tesseract_is_run_on_one_thread_so_stills_read_side_by_side_do_not_fight_for_the_cores(self):
        import sign_finder
        self.assertEqual(sign_finder.ONE_THREAD["OMP_THREAD_LIMIT"], "1")

    def test_a_frame_without_one_gives_nothing(self):
        import numpy as np
        self.assertEqual(SC.find_school(np.full((700, 1000, 3), (110, 125, 120), np.uint8)), [])


class Signs(unittest.TestCase):
    def test_a_minute_gives_one_school_zone_with_the_hours_most_stills_agree_on(self):
        d = tempfile.mkdtemp()
        try:
            rv = AR.Review(d)
            rv.days["2026-10-26"] = {"2026-10-26_16-51-37": dict(n=60, d=[
                dict(s=10, text="School zone", kind="school", c=70, x=1, y=1, r=20, side="R", tm=[], tc=0, f="a.jpg"),
                dict(s=11, text="School zone", kind="school", c=75, x=1, y=1, r=30, side="R", tm=["08:00-09:30", "14:30-16:00"], tc=80, f="b.jpg"),
                dict(s=12, text="School zone", kind="school", c=72, x=1, y=1, r=34, side="R", tm=["08:00-09:30", "14:30-16:00"], tc=85, f="c.jpg"),
                dict(s=12, text="40", c=95, x=1, y=1, r=20, side="L", f="d.jpg")])}
            sg = rv.signs(["2026-10-26_16-51-37"])
            sz = [g for g in sg if g["kind"] == "school"]
            self.assertEqual(len(sz), 1)
            self.assertEqual((sz[0]["times"], sz[0]["crop"], sz[0]["n"]), (["08:00-09:30", "14:30-16:00"], "c.jpg", 3))
            self.assertEqual([g["times"] for g in sg if g["kind"] != "school"], [[]])
        finally:
            shutil.rmtree(d)

    def test_the_second_look_leaves_school_zone_signs_alone_and_asks_again_only_when_no_hours_were_read(self):
        d = [dict(s=10, text="School zone", kind="school", c=70, x=1, y=1, r=20, side="R"), dict(s=30, text="60", c=95, x=1, y=1, r=20, side="R")]
        self.assertEqual([sp[3] for sp in AR._spans(d, 2.0)], [[1]])


if __name__ == "__main__":
    unittest.main()
