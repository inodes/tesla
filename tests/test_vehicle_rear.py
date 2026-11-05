"""vehicle_rear.on_vehicle: a sign between a pair of tail lamps a bus's width apart is on a vehicle; a sign alone, over a car's lamps, or off-centre is not."""
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib"))
import vehicle_rear as V  # noqa: E402

R = 30      # sign radius in pixels
X, Y = 400, 200


def frame(lamps):
    im = np.full((600, 800, 3), (200, 170, 120), np.uint8)
    cv2.circle(im, (X, Y), R, (40, 40, 200), -1)
    cv2.circle(im, (X, Y), int(R * .7), (240, 240, 240), -1)
    for dx, dy in lamps:
        cv2.rectangle(im, (int(X + dx * R) - 6, int(Y + dy * R) - 4), (int(X + dx * R) + 6, int(Y + dy * R) + 4), (20, 20, 230), -1)
    return im


class VehicleRearTest(unittest.TestCase):
    def test_bus_rear(self):
        self.assertTrue(V.on_vehicle(frame([(-4.2, 3), (4.2, 3)]), X, Y, R))

    def test_sign_alone(self):
        self.assertFalse(V.on_vehicle(frame([]), X, Y, R))

    def test_a_cars_lamps_are_too_close(self):
        self.assertFalse(V.on_vehicle(frame([(-2.3, 4), (2.3, 4)]), X, Y, R))

    def test_one_lamp_is_not_a_pair(self):
        self.assertFalse(V.on_vehicle(frame([(4.2, 3)]), X, Y, R))

    def test_lamps_not_level(self):
        self.assertFalse(V.on_vehicle(frame([(-4.2, 0), (4.2, 5)]), X, Y, R))

    def test_sign_not_centred_between_lamps(self):
        self.assertFalse(V.on_vehicle(frame([(-3.0, 3), (6.0, 3)]), X, Y, R))

    def test_works_at_any_scale(self):
        im = cv2.resize(frame([(-4.2, 3), (4.2, 3)]), (400, 300))
        self.assertTrue(V.on_vehicle(im, X / 2, Y / 2, R / 2))


if __name__ == "__main__":
    unittest.main()
