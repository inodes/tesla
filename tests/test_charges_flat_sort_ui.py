#!/usr/bin/env python3
"""test_charges_flat_sort_ui.py - Charges is a flat table, not grouped by day: one sort for date and time together (newest first / oldest first), not the two icons of the day-grouped pages.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_charges_flat_sort_ui.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
JS = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()


class FlatSortTest(unittest.TestCase):
    def test_one_button_for_a_flat_table(self):
        self.assertIn("const ordOne=()=>", JS)
        self.assertIn("ordOne()", JS)
        self.assertIn('(flat?ordOne():ordBtns())', JS)
        self.assertIn('rangeBar($("#rg"),"charges",CP.r,daysOf(P),show,true)', JS)

    def test_charges_sorts_on_the_time_alone(self):
        self.assertIn('if(CP.sk==="t0")return ordFlat(a,b)', JS)
        self.assertIn("ordFlat=(a,b)=>(ORD.dayDesc?-1:1)*((a.t0||0)-(b.t0||0))", JS)

    def test_the_grouped_pages_keep_both(self):
        for key in ("parked", "driveslist", "teslacam"):
            i = JS.index('rangeBar($("#rg"),"%s"' % key)
            self.assertNotIn(",true);", JS[i:i + 400].split(";")[0] + ";")


if __name__ == "__main__":
    unittest.main()
