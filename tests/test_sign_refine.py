"""test_sign_refine.py - the second look at the signs of a minute (app_review.refine_clip): stills every 0.2 s around each sign that was seen once or read unsurely, a vote on the number.

    python3 -m unittest tests/test_sign_refine.py
"""
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_review as AR  # noqa: E402


def det(s, text, c, side="L", r=20, f=""):
    return dict(s=s, text=text, c=c, x=1, y=1, r=r, side=side, f=f)


class Crop:
    shape = (40, 40)


class Pure(unittest.TestCase):
    def test_a_sign_seen_in_one_still_gets_a_second_look_and_one_read_surely_in_several_does_not(self):
        d = [det(10, "60", 95), det(30, "80", 90), det(31, "80", 92), det(32, "80", 96)]
        spans = AR._spans(d, 2.0)
        self.assertEqual([(s[0], s[1], s[2], s[3]) for s in spans], [("L", 8, 12, [0]), ("L", 28, 34, [1, 2, 3])])
        self.assertEqual([AR._weak(d, s[3]) for s in spans], [True, False])

    def test_unsure_readings_and_disagreeing_ones_are_weak(self):
        self.assertTrue(AR._weak([det(1, "60", 70), det(2, "60", 72)], [0, 1]))
        self.assertTrue(AR._weak([det(1, "60", 95), det(2, "80", 95)], [0, 1]))

    def test_the_sides_are_looked_at_separately(self):
        d = [det(10, "60", 60, "L"), det(10, "60", 60, "R")]
        self.assertEqual([s[0] for s in AR._spans(d, 2.0)], ["L", "R"])

    def test_the_vote_goes_by_how_sure_each_reading_was_and_keeps_the_clearest_three(self):
        old = [det(10, "80", 55)]
        new = [det(10.2, "60", 90, r=30), det(10.4, "60", 88, r=40), det(10.6, "60", 70, r=10), det(10.8, "60", 91, r=50), det(11, "80", 60)]
        win, keep, best = AR._vote(old, new)
        self.assertEqual(win, "60")
        self.assertEqual(keep, [])
        self.assertEqual([x["s"] for x in best], [10.8, 10.4, 10.2])


class Refine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "crops"))
        self.written = []
        fake = types.SimpleNamespace(imread=lambda f: object(), resize=lambda c, sz: c, imwrite=lambda p, c, o=None: (open(p, "wb").write(b"j"), self.written.append(p)) and True,
                                     IMWRITE_JPEG_QUALITY=1)
        self._cv2 = sys.modules.get("cv2")
        sys.modules["cv2"] = fake
        self._fa, self._lk = AR._frames_at, AR._look
        AR._frames_at = lambda path, start, dur, step, tmp: [(start + k * step, "f%d" % k) for k in range(int(dur / step))]
        self.addCleanup(self.restore)

    def restore(self):
        AR._frames_at, AR._look = self._fa, self._lk
        if self._cv2 is None:
            sys.modules.pop("cv2", None)
        else:
            sys.modules["cv2"] = self._cv2

    def test_the_finer_stills_outvote_a_wrong_reading_and_its_picture_goes(self):
        old = os.path.join(self.tmp, "crops", "S_00.jpg")
        open(old, "wb").write(b"j")
        AR._look = lambda im, side: dict(text="60", conf=90, x=1, y=1, r=30, crop=Crop(), ok=True)
        rec = dict(n=60, d=[det(10, "80", 52, f="S_00.jpg")])
        out = AR.refine_clip("x.mp4", "S", rec, self.tmp)
        self.assertEqual(out["rf"], 1)
        self.assertEqual({x["text"] for x in out["d"]}, {"60"})
        self.assertEqual(len(out["d"]), 3)                                           # the clearest three
        self.assertFalse(os.path.exists(old))
        self.assertTrue(all(os.path.exists(os.path.join(self.tmp, "crops", x["f"])) for x in out["d"]))

    def test_a_sign_that_is_already_sure_is_marked_done_without_looking(self):
        AR._frames_at = lambda *a: self.fail("the video was read")
        rec = dict(n=60, d=[det(10, "60", 95), det(11, "60", 96)])
        out = AR.refine_clip("x.mp4", "S", rec, self.tmp)
        self.assertEqual((out["rf"], out["d"]), (1, rec["d"]))

    def test_nothing_found_in_the_finer_stills_keeps_what_was_read(self):
        AR._look = lambda im, side: None
        rec = dict(n=60, d=[det(10, "60", 60)])
        out = AR.refine_clip("x.mp4", "S", rec, self.tmp)
        self.assertEqual((out["rf"], [x["text"] for x in out["d"]]), (1, ["60"]))


class Picture(unittest.TestCase):
    def test_the_picture_of_a_sign_is_the_biggest_one_it_was_read_surely_in(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        rv = AR.Review(tmp)
        rv._day("2026-10-16")["2026-10-16_15-40-07"] = dict(n=60, d=[det(10, "60", 95, r=15, f="far.jpg"), det(11, "60", 85, r=40, f="near.jpg"), det(12, "60", 90, r=25, f="mid.jpg")])
        self.assertEqual(rv.minute_signs(["2026-10-16_15-40-07"])["2026-10-16_15-40-07"][0]["f"], "near.jpg")
        self.assertEqual(rv.signs(["2026-10-16_15-40-07"])[0]["crop"], "near.jpg")


class Job(unittest.TestCase):
    def test_refine_runs_once_per_minute_and_unrefined_lists_the_rest(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        rv = AR.Review(tmp)
        rv._day("2026-10-16")["2026-10-16_15-40-07"] = dict(n=60, d=[det(10, "60", 60)])
        rv._day("2026-10-16")["2026-10-16_15-41-07"] = dict(n=60, d=[])
        self.assertEqual(rv.unrefined(), set())          # nothing is saved on disk yet: only the saved days are listed
        rv._save("2026-10-16")
        self.assertEqual(rv.unrefined(), {"2026-10-16_15-40-07"})
        calls = []
        orig, req = AR.refine_clip, AR.requirements
        AR.refine_clip = lambda path, stamp, rec, base: calls.append(stamp) or dict(rec, rf=1)
        AR.requirements = lambda: dict(py=[], cmd=[])
        try:
            rv.refine([("2026-10-16_15-40-07", "x.mp4"), ("2026-10-16_15-41-07", "y.mp4")])
            rv.t.join(5)
            self.assertEqual(calls, ["2026-10-16_15-40-07"])
            self.assertEqual(rv.unrefined(), set())
            rv.refine([("2026-10-16_15-40-07", "x.mp4")])
            if rv.t:
                rv.t.join(5)
            self.assertEqual(calls, ["2026-10-16_15-40-07"])                              # not again
        finally:
            AR.refine_clip, AR.requirements = orig, req


if __name__ == "__main__":
    unittest.main()
