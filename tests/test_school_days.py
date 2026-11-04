#!/usr/bin/env python3
"""test_school_days.py - is a date a school day in a state (resources/school_terms.json + the state's public holidays)? Made-up calendar for the rules, the real file for its shape.

    python3 -m unittest tests/test_school_days.py
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import school_days as SD  # noqa: E402


def calendar():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.json")
    with open(p, "w") as f:
        json.dump({"NSW": {"terms": {"2026": [["2026-02-02", "2026-04-02"], ["2026-10-13", "2026-12-17"]]}}, "QLD": {"terms": {"2026": [["2026-01-27", "2026-03-27"]]}}}, f)
    return p


class SchoolDaysTest(unittest.TestCase):
    def test_a_weekday_inside_a_term_is_a_school_day(self):
        p = calendar()
        self.assertTrue(SD.school_day("NSW", "2026-11-04", p))                      # a Wednesday in Term 4
        self.assertTrue(SD.school_day("NSW", datetime.date(2026, 10, 13), p))       # the first and last days count
        self.assertTrue(SD.school_day("NSW", "2026-12-17", p))

    def test_weekends_holidays_and_the_gaps_between_terms_are_not(self):
        p = calendar()
        self.assertFalse(SD.school_day("NSW", "2026-11-07", p))                     # a Saturday
        self.assertFalse(SD.school_day("NSW", "2026-12-18", p))                     # after the term
        self.assertFalse(SD.school_day("NSW", "2026-07-01", p))                     # the school holidays
        self.assertFalse(SD.school_day("NSW", "2026-04-03", p))                     # after Term 1 (Good Friday)
        self.assertTrue(SD.school_day("NSW", "2026-02-02", p))                      # the first day of Term 1 (a Monday) is one

    def test_a_public_holiday_in_term_is_not_a_school_day_in_nsw(self):
        q = os.path.join(tempfile.mkdtemp(), "t.json")
        with open(q, "w") as f:
            json.dump({"NSW": {"terms": {"2026": [["2026-01-01", "2026-12-31"]]}}}, f)
        self.assertFalse(SD.school_day("NSW", "2026-01-26", q))                     # Australia Day, a Monday
        self.assertFalse(SD.school_day("NSW", "2026-12-25", q))                     # Christmas Day, a Friday
        self.assertTrue(SD.school_day("NSW", "2026-01-27", q))

    def test_an_unknown_state_or_year_is_none_not_a_guess(self):
        p = calendar()
        self.assertIsNone(SD.school_day("VIC", "2026-11-04", p))
        self.assertIsNone(SD.school_day("NSW", "2031-11-04", p))
        self.assertIsNone(SD.school_day(None, "2026-11-04", p))
        self.assertIsNone(SD.school_day("NSW", "not a date", p))

    def test_a_state_without_holiday_rules_counts_the_weekday_and_the_term(self):
        p = calendar()
        self.assertTrue(SD.school_day("QLD", "2026-02-10", p))
        self.assertFalse(SD.school_day("QLD", "2026-02-14", p))                     # a Saturday

    def test_the_calendar_file_in_the_repo_is_well_formed(self):
        cal = SD.terms()
        self.assertIn("NSW", cal)
        for year, rs in cal["NSW"]["terms"].items():
            self.assertEqual(len(rs), 4)
            last = ""
            for a, b in rs:
                self.assertTrue(last < a <= b, (a, b))
                self.assertTrue(a.startswith(year) and b.startswith(year))
                self.assertEqual(datetime.date.fromisoformat(a).weekday() < 5 and datetime.date.fromisoformat(b).weekday() < 5, True)
                last = b


if __name__ == "__main__":
    unittest.main()
