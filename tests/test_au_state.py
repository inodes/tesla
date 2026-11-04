#!/usr/bin/env python3
"""test_au_state.py - the state label of a position (lib/au_state.py): towns on both sides of the borders that matter on a Gold Coast - Sydney - Melbourne drive.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_au_state.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import au_state as A  # noqa: E402

CASES = dict(MainBeach=((-27.98, 153.43), "QLD"), Brighton=((-27.31, 153.05), "QLD"), Tweed=((-28.2, 153.54), "NSW"), Byron=((-28.64, 153.61), "NSW"), Coffs=((-30.3, 153.11), "NSW"),
             Sydney=((-33.87, 151.2), "NSW"), Melbourne=((-37.81, 144.96), "VIC"), Canberra=((-35.28, 149.13), "ACT"), Adelaide=((-34.93, 138.6), "SA"), Perth=((-31.95, 115.86), "WA"),
             Darwin=((-12.46, 130.84), "NT"), Hobart=((-42.88, 147.33), "TAS"), Albury=((-36.08, 146.92), "NSW"), Wodonga=((-36.12, 146.89), "VIC"), Mildura=((-34.2, 142.15), "VIC"),
             BrokenHill=((-31.95, 141.45), "NSW"), Toowoomba=((-27.56, 151.95), "QLD"), Goondiwindi=((-28.55, 150.31), "QLD"), Eden=((-37.07, 149.9), "NSW"), Orbost=((-37.7, 148.46), "VIC"))


class StateTest(unittest.TestCase):
    def test_towns(self):
        for name, (p, want) in CASES.items():
            self.assertEqual(A.state(*p), want, name)

    def test_outside_australia_and_missing(self):
        self.assertIsNone(A.state(10, 10))
        self.assertIsNone(A.state(None, 153))


if __name__ == "__main__":
    unittest.main()
