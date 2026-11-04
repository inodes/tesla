"""The trained speed-sign reader: loads, reads a plain drawn sign, and does not trust a blank face."""
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib"))
import sign_reader as SR  # noqa: E402


def drawn(text, n=80):
    im = np.full((n, n, 3), 150, np.uint8)
    c = n // 2
    cv2.circle(im, (c, c), int(n * .48), (40, 40, 200), -1)
    cv2.circle(im, (c, c), int(n * .36), (235, 235, 235), -1)
    cv2.putText(im, text, (int(n * .2), int(n * .6)), cv2.FONT_HERSHEY_SIMPLEX, n / 40 * .75, (20, 20, 20), 2)
    r = int(n * .48 * .74)
    return im[c - r:c + r, c - r:c + r]


class SignReaderTest(unittest.TestCase):
    def test_weights_load(self):
        self.assertTrue(SR.available())

    def test_reads_drawn_limits(self):
        for t in ("40", "60"):
            text, conf = SR.read(drawn(t))
            self.assertEqual(text, t)
            self.assertGreater(conf, 90)

    def test_blank_face_is_weak(self):
        text, conf = SR.read(np.full((30, 30, 3), 200, np.uint8))
        self.assertLess(conf, 50)

    def test_features_shape(self):
        self.assertEqual(SR.features(drawn("40")).shape, (1024,))


if __name__ == "__main__":
    unittest.main()
