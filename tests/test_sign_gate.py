#!/usr/bin/env python3
"""test_sign_gate.py - what keeps a car bumper, a tree or a roundabout board from being read as a speed limit (docs/SIGNS.md):
the red-ring check on a circle, the school zone hours snap, and the sudden-jump flag of the HUD trust (run in node).

    python3 -m unittest tests/test_sign_gate.py
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import node_usable  # noqa: E402

try:
    import cv2
    import numpy as np
except ImportError:                       # pragma: no cover
    cv2 = None

APP_JS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js")


def sign(ring=True, face=(215, 215, 215), r=40, size=200):
    im = np.full((size, size, 3), (110, 125, 120), np.uint8)             # a greenish dashcam road
    c = (size // 2, size // 2)
    cv2.circle(im, c, r, face, -1)
    if ring:
        cv2.circle(im, c, r, (40, 30, 200), max(3, r // 6))               # BGR red ring
    cv2.putText(im, "70", (c[0] - r // 2, c[1] + r // 5), cv2.FONT_HERSHEY_SIMPLEX, r / 35.0, (20, 20, 20), 2)
    return im


@unittest.skipIf(cv2 is None, "opencv is not installed")
class RingTest(unittest.TestCase):
    def test_a_red_ring_round_a_face_passes(self):
        import sign_finder as SF
        rc = SF.ring_check(sign(), 100, 100, 40)
        self.assertTrue(rc["ok"], rc)
        self.assertGreaterEqual(rc["cover"], 0.8)

    def test_a_grey_dashcam_face_still_passes(self):
        import sign_finder as SF
        self.assertTrue(SF.ring_check(sign(face=(125, 130, 128)), 100, 100, 40)["ok"])

    def test_no_ring_fails(self):
        import sign_finder as SF
        self.assertFalse(SF.ring_check(sign(ring=False), 100, 100, 40)["ok"])

    def test_a_solid_red_disc_fails(self):
        import sign_finder as SF
        im = np.full((200, 200, 3), (110, 125, 120), np.uint8)
        cv2.circle(im, (100, 100), 40, (40, 30, 200), -1)
        rc = SF.ring_check(im, 100, 100, 40)
        self.assertFalse(rc["ok"], rc)           # a tail light: the face is red

    def test_a_half_ring_fails(self):
        import sign_finder as SF
        im = sign(ring=False)
        cv2.ellipse(im, (100, 100), (40, 40), 0, 0, 150, (40, 30, 200), 7)
        self.assertFalse(SF.ring_check(im, 100, 100, 40)["ok"])


@unittest.skipIf(cv2 is None, "opencv is not installed")
class SchoolSnapTest(unittest.TestCase):
    def test_the_lost_thirty_is_put_back(self):
        import sign_classes as SC
        self.assertEqual(SC.snap_times(["08:00-09:00", "14:00-16:00"]), (["08:00-09:30", "14:30-16:00"], True))
        self.assertEqual(SC.snap_times(["08:00-09:00", "15:00-16:00"]), (["08:00-09:30", "14:30-16:00"], True))

    def test_the_standard_hours_are_not_marked_snapped(self):
        import sign_classes as SC
        self.assertEqual(SC.snap_times(["08:00-09:30", "14:30-16:00"]), (["08:00-09:30", "14:30-16:00"], False))

    def test_other_hours_are_left_as_read(self):
        import sign_classes as SC
        self.assertEqual(SC.snap_times(["09:00-10:00"]), (["09:00-10:00"], False))
        self.assertEqual(SC.snap_times([]), ([], False))


def run(expr):
    with open(APP_JS, encoding="utf-8") as f:
        src = f.read()
    h = src[src.index("// HUD begin"):src.index("// HUD end")]
    out = subprocess.run(["node", "-e", h + "\nconsole.log(JSON.stringify(" + expr + "))"], capture_output=True, text=True)
    return json.loads(out.stdout)


@unittest.skipUnless(node_usable.usable(), "node is not installed or does not start")
class JumpTest(unittest.TestCase):
    def all(self, rows):
        return json.dumps([dict(t=t, text=str(x)) for t, x in rows])

    def test_a_130_after_a_50_with_the_car_at_50_is_a_jump(self):
        a = self.all([(0, 50), (60, 130)])
        self.assertTrue(run("hudJump(60,'130',%s,k=>50)" % a))

    def test_a_small_step_is_not(self):
        a = self.all([(0, 50), (60, 60)])
        self.assertFalse(run("hudJump(60,'60',%s,k=>50)" % a))

    def test_another_sign_of_that_number_confirms_it(self):
        a = self.all([(0, 50), (60, 100), (90, 100)])
        self.assertFalse(run("hudJump(60,'100',%s,k=>50)" % a))

    def test_repeat_reads_of_one_sign_do_not_confirm_it(self):
        a = self.all([(0, 50), (60, 100), (61, 100), (62, 100)])
        self.assertTrue(run("hudJump(60,'100',%s,k=>50)" % a))

    def test_the_car_doing_it_confirms_it(self):
        a = self.all([(0, 50), (60, 100)])
        self.assertFalse(run("hudJump(60,'100',%s,k=>95)" % a))

    def test_an_old_sign_does_not_count(self):
        a = self.all([(0, 50), (500, 110)])
        self.assertFalse(run("hudJump(500,'110',%s,null)" % a))

    def test_the_jump_cuts_trust_and_is_named(self):
        g = "{text:'130',conf:100}"
        j = run("hudTrust(%s,null,[],false,true)" % g)
        n = run("hudTrust(%s,null,[],false,false)" % g)
        self.assertTrue(j["q"])
        self.assertAlmostEqual(j["t"], n["t"] * 0.4, places=3)


if __name__ == "__main__":
    unittest.main()
