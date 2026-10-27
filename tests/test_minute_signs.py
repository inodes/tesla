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


if __name__ == "__main__":
    unittest.main()
