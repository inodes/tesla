#!/usr/bin/env python3
"""Addresses carry their state, and a drive that crosses a clock change reads on both clocks (start where it began, end where it ended)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
from lib import app_data as AD  # noqa: E402
import teslacam_timeline as TL  # noqa: E402

AEST, AEDT = 36000.0, 39600.0
# Sunday 11 Oct 2026, daylight saving having started in NSW on 4 Oct: Queensland stays on AEST
T0 = 1791693600          # 04:40 UTC, 14:40 AEST at Main Beach (Queensland)
T1 = 1791700500          # 06:35 UTC, 17:35 AEDT at New Italy (New South Wales), 16:35 on the Queensland clock
MAIN_BEACH, NEW_ITALY = [-27.9796, 153.4252], [-29.1536, 153.297]


class AddressStateTest(unittest.TestCase):
    def test_state_is_kept_short(self):
        self.assertEqual(AD._short("29-39 Example Avenue, Main Beach Queensland 4217, Australia"), "29-39 Example Avenue, Main Beach, QLD")
        self.assertEqual(AD._short("1 Example Street, Sampletown New South Wales 2000, Australia"), "1 Example Street, Sampletown, NSW")
        self.assertEqual(AD._short("2 Example Road, Sampletown Victoria 3000, Australia"), "2 Example Road, Sampletown, VIC")
        self.assertEqual(AD._short("3 Example Lane, Sampletown WA 6000, Australia"), "3 Example Lane, Sampletown, WA")

    def test_no_state_in_the_text_adds_none(self):
        self.assertEqual(AD._short("5 Example Street"), "5 Example Street")
        self.assertEqual(AD._short("5 Example Street, Sampletown"), "5 Example Street, Sampletown")
        self.assertEqual(AD._short(""), "")
        self.assertEqual(AD._short(None), "")


class DriveClockTest(unittest.TestCase):
    def test_border_drive_start_and_end_on_their_own_clocks(self):
        it = TL.show(dict(t0=T0, t1=T1, p0=MAIN_BEACH, p1=NEW_ITALY))
        self.assertEqual((it["lo"], it["lo1"]), (AEST, AEDT))
        self.assertEqual((it["start"], it["end"]), ("2026-10-11T14:40:00", "2026-10-11T17:35:00"))        # not 16:35: that is the end on the start's clock

    def test_same_zone_drive_has_one_clock(self):
        it = TL.show(dict(t0=T1, t1=T1 + 600, p0=NEW_ITALY, p1=NEW_ITALY))
        self.assertEqual((it["lo"], it["lo1"]), (AEDT, AEDT))
        self.assertEqual((it["start"], it["end"]), ("2026-10-11T17:35:00", "2026-10-11T17:45:00"))

    def test_given_clock_is_used_for_both(self):
        it = TL.show(dict(t0=T0, t1=T1, p0=MAIN_BEACH, p1=NEW_ITALY), lo=AEST)
        self.assertEqual((it["lo"], it["lo1"], it["end"]), (AEST, AEST, "2026-10-11T16:35:00"))

    def test_no_position_falls_back_to_the_app_clock(self):
        it = TL.show(dict(t0=T0, t1=T1))
        self.assertEqual(it["lo"], it["lo1"])


if __name__ == "__main__":
    unittest.main()
