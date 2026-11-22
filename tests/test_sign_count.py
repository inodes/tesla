"""Review.sign_count gives the number of signs signs() makes (the keeper counts them for every clip), without making them."""
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
import app_review as AR  # noqa: E402


class SignCountTest(unittest.TestCase):
    def test_the_same_as_the_signs_made(self):
        rnd = random.Random(7)
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "drive_review"))
        day, stamps = {}, []
        for m in range(30):
            st = "2026-10-26_08-%02d-00" % m
            stamps.append(st)
            day[st] = dict(n=60, d=[dict(s=rnd.uniform(0, 59), text=rnd.choice(["40", "60", "80"]), c=rnd.randint(30, 99), side=rnd.choice("LR"), f="x.jpg")
                                    for _ in range(rnd.randint(0, 8))])
        with open(os.path.join(d, "drive_review", "2026-10-26.json"), "w") as f:
            json.dump(day, f)
        r = AR.Review(d)
        for k in (1, 3, 7, 30):
            for i in range(0, 30, k):
                self.assertEqual(r.sign_count(stamps[i:i + k]), len(r.signs(stamps[i:i + k])))
        self.assertEqual(r.sign_count([]), 0)


if __name__ == "__main__":
    unittest.main()
