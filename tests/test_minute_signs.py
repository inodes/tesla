import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
import app_review as AR  # noqa: E402

ST = "2026-10-26_08-54-39"


class MinuteSignsTest(unittest.TestCase):
    def test_readings_of_one_sign_in_a_minute_are_one_sign_with_its_seconds(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "drive_review"))
        rows = [dict(s=9.0, text="60", c=93, side="R", f="a.jpg"), dict(s=9.8, text="60", c=54, side="R", f="b.jpg"),
                dict(s=30.0, text="80", c=88, side="L", f="c.jpg"), dict(s=9.4, text="60", c=95, side="R", f="d.jpg")]
        with open(os.path.join(d, "drive_review", "2026-10-26.json"), "w") as f:
            json.dump({ST: dict(n=14, d=rows)}, f)
        r = AR.Review(d)
        got = r.minute_signs([ST, "2026-10-26_08-55-39"])
        self.assertEqual(list(got), [ST])                                             # a minute with no reading is left out
        a, b = got[ST]
        self.assertEqual((a["text"], a["s0"], a["s1"], a["n"], a["conf"], a["f"]), ("60", 9.0, 9.8, 3, 95, "d.jpg"))
        self.assertEqual((b["text"], b["side"], b["s0"], b["n"]), ("80", "L", 30.0, 1))

    def test_the_picture_of_a_sign_is_the_biggest_one_that_was_read_surely(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "drive_review"))
        rows = [dict(s=9.0, text="60", c=90, side="L", r=20, f="far.jpg"), dict(s=10.0, text="60", c=85, side="L", r=60, f="near.jpg"),
                dict(s=11.0, text="60", c=40, side="L", r=90, f="blur.jpg")]
        with open(os.path.join(d, "drive_review", "2026-10-26.json"), "w") as f:
            json.dump({ST: dict(n=14, d=rows)}, f)
        g = AR.Review(d).minute_signs([ST])[ST][0]
        self.assertEqual((g["f"], g["conf"]), ("near.jpg", 85))                       # not the far one, and not a big blurred one
        self.assertNotIn("_sc", g)

    def test_a_school_zone_carries_the_hours_most_of_its_stills_agree_on(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "drive_review"))
        rows = [dict(s=31.0, text="School zone", kind="school", c=50, side="L", tm=["08:00-09:30", "14:30-16:00"], tc=90, f="a.jpg"),
                dict(s=31.4, text="School zone", kind="school", c=50, side="L", tm=["08:00-09:30"], tc=10, f="b.jpg"),
                dict(s=31.8, text="School zone", kind="school", c=40, side="L", tm=[], tc=0, f="c.jpg"),
                dict(s=40.0, text="40", c=95, side="R", f="d.jpg")]
        with open(os.path.join(d, "drive_review", "2026-10-26.json"), "w") as f:
            json.dump({ST: dict(n=14, d=rows)}, f)
        z, sp = AR.Review(d).minute_signs([ST])[ST]
        self.assertEqual(z["times"], ["08:00-09:30", "14:30-16:00"])
        self.assertEqual(sp["times"], [])                                           # a speed sign has no hours
        self.assertNotIn("_tm", z)


if __name__ == "__main__":
    unittest.main()
