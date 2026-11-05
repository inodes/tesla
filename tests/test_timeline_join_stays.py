#!/usr/bin/env python3
"""test_timeline_join_stays.py - a park the dashcam recorded in pieces is one stop (teslacam_timeline.join_stays), decided by what the cameras saw (park_scene), else by the places.

    python3 -m unittest tests/test_timeline_join_stays.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib"))
import park_scene as PS  # noqa: E402
import teslacam_timeline as TL  # noqa: E402

HOME = [-33.80653, 151.07962]
FAR = [-33.9, 151.2]


def it(i, kind, t0, t1, p0=None, p1=None, n=1):
    return dict(id=i, kind=kind, t0=t0, t1=t1, start="s%d" % t0, end="e%d" % t1, lo=0, lo1=0, n=n, recent=n, saved=0, read=0, p0=p0, p1=p1)


def gap(t0, t1):
    return dict(id="g:%d" % t0, kind="gap", t0=t0, t1=t1, start="", end="", p0=None, p1=None)


def day(first_p0=None, drive_end=HOME, drive_start=HOME):
    """a drive that ends at home, four pieces of a stay (the first beside the drive), a drive from home; newest first"""
    m = 60
    L = [it("d1", "drive", 0, 100 * m, p0=FAR, p1=drive_end, n=36),
         it("s1", "stopped", 100 * m, 102 * m, p0=first_p0, p1=first_p0), gap(102 * m, 142 * m),
         it("s2", "stopped", 142 * m, 146 * m, n=4), gap(146 * m, 171 * m),
         it("s3", "stopped", 171 * m, 172 * m), gap(172 * m, 185 * m),
         it("s4", "stopped", 185 * m, 186 * m), gap(186 * m, 400 * m),
         it("d2", "drive", 400 * m, 407 * m, p0=drive_start, p1=FAR)]
    return sorted(L, key=lambda x: x["t0"], reverse=True)


def ids(L):
    return [x["id"] for x in sorted(L, key=lambda x: x["t0"])]


class JoinStaysTest(unittest.TestCase):
    def test_pictures_say_it_stayed(self):
        L = TL.join_stays(day(first_p0=HOME), lambda a, b: True)
        self.assertEqual(ids(L), ["d1", "s1", "g:%d" % (186 * 60), "d2"])
        s = next(x for x in L if x["id"] == "s1")
        self.assertEqual((s["t0"], s["t1"], s["n"], s["joined"]), (100 * 60, 186 * 60, 1 + 4 + 1 + 1, 4))
        self.assertEqual(L[0]["t0"] > L[-1]["t0"], True)      # the order it came in

    def test_pictures_say_it_moved(self):
        L = TL.join_stays(day(first_p0=HOME), lambda a, b: False)
        self.assertEqual([x["id"] for x in L if x["kind"] == "stopped"], ["s4", "s3", "s2", "s1"])

    def test_not_looked_at_yet_the_places_decide(self):
        self.assertEqual(len([x for x in TL.join_stays(day(first_p0=HOME), lambda a, b: None) if x["kind"] == "stopped"]), 1)
        # no place on the pieces: the drive before and the drive after are at one place -> one park
        self.assertEqual(len([x for x in TL.join_stays(day(), None) if x["kind"] == "stopped"]), 1)
        # ... at two places: a drive we are not seeing in between -> not joined
        self.assertEqual(len([x for x in TL.join_stays(day(drive_start=FAR), None) if x["kind"] == "stopped"]), 4)

    def test_a_long_hole_is_not_joined(self):
        L = [it("d1", "drive", 0, 600, p1=HOME), it("s1", "stopped", 600, 660, p0=HOME, p1=HOME), gap(660, 660 + 7200),
             it("s2", "stopped", 660 + 7200, 660 + 7260, p0=HOME, p1=HOME)]
        self.assertEqual(len([x for x in TL.join_stays(L, lambda a, b: True) if x["kind"] == "stopped"]), 2)

    def test_a_drive_between_keeps_them_apart(self):
        L = [it("s1", "stopped", 0, 60, p0=HOME, p1=HOME), gap(60, 600), it("dr", "drive", 600, 900, p0=HOME, p1=HOME),
             it("s2", "stopped", 900, 960, p0=HOME, p1=HOME)]
        self.assertEqual(len([x for x in TL.join_stays(L, lambda a, b: True) if x["kind"] == "stopped"]), 2)


def grey(f):
    return bytes(int(f(i)) & 255 for i in range(PS.W * PS.H))


class SceneTest(unittest.TestCase):
    def test_same_and_not(self):
        a = {c: grey(lambda i, k=k: (i * (3 + k)) % 200) for k, c in enumerate(["front", "back", "left"])}
        bright = {c: bytes(min(255, v + 20) for v in a[c]) for c in a}      # the light changed: still the place
        other = {c: grey(lambda i, k=k: ((i * 7 + 13 * k) ** 2) % 251) for k, c in enumerate(["front", "back", "left"])}
        self.assertTrue(PS.same(a, bright))
        self.assertFalse(PS.same(a, other))
        self.assertIsNone(PS.same(a, {}))
        self.assertIsNone(PS.same(a, {"right": a["front"]}))


if __name__ == "__main__":
    unittest.main()
