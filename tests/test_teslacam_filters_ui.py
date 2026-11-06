#!/usr/bin/env python3
"""test_teslacam_filters_ui.py - the TeslaCam page's filters are drop-downs that start at "all", are never kept across a start, can be cleared at once, and
the days / times have sort arrows (checked as text; the page needs a browser).

    python3 -m unittest tests/test_teslacam_filters_ui.py
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class TeslaCamFiltersUiTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_four_filters_all_by_default(self):
        self.assertIn('f:{drives:[],loc:"all",type:"all",ev:"all"}', self.js)
        for sel in ('id="floc"', 'id="ftype"', 'id="fev"', 'id="fdr"'):
            self.assertIn(sel, self.js)
        self.assertIn('opt("home",homeNames().join(", "),f.loc)', self.js)
        self.assertIn('opt("saved","Saved locations",f.loc)', self.js)
        self.assertIn('opt("driving","Driving",f.type)', self.js)
        self.assertIn('opt("parked","Parked",f.type)', self.js)

    def test_the_old_show_buttons_are_gone(self):
        self.assertNotIn(">Show:</span>", self.js)
        self.assertNotIn("TCS.src", self.js)
        self.assertNotIn("TCS.at[", self.js)

    def test_filters_are_not_kept(self):
        tc = self.js[self.js.index("async function pageTeslaCam("):]
        self.assertNotRegex(tc[:60000], r"(localStorage|lsSet|IDB\.set)\([^)]*(TCS\.f|tc\.filter)")

    def test_clear_filters_and_selection_follow_what_is_shown(self):
        self.assertIn('id="fclr"', self.js)
        self.assertIn('TCS.f={drives:[],loc:"all",type:"all",ev:"all"};TCS.q=""', self.js)
        self.assertIn("if(!vis.some(v=>v.ty+\"|\"+v.id===k))TCSEL.delete(k)", self.js)       # Move / Delete never act on a hidden clip

    def test_sort_arrows(self):
        # one order of the days (a calendar and an arrow) and of the times in a day (a clock and an arrow), beside the date bar of every list: Drives, Charges, Parked, TeslaCam
        self.assertIn("${calSvg}${ARW(ORD.dayDesc)}", self.js)
        self.assertIn("${clkSvg}${ARW(ORD.rowDesc)}", self.js)
        self.assertIn("(flat?ordOne():ordBtns())", self.js)                                   # drawn by the date bar itself
        self.assertNotIn('<span class="sub">Sort</span>', self.js)
        self.assertNotIn('id="daysort"', self.js)                               # no page has a bar of its own for it
        self.assertNotIn("TCS.dayDesc", self.js)
        self.assertNotIn("TLS.dayDesc", self.js)
        self.assertIn("(ORD.dayDesc?days.slice(0,60):days.slice(0,60).reverse())", self.js)
        self.assertIn('cfg("teslacam.days_newest_first")!==false', self.js)       # days: newest first unless set otherwise; times: oldest first (rows_newest_first false)
        self.assertIn('lsSet("ord.days"', self.js)                              # and remembered
        for fn in ("const ordered=sk===\"t0\"", "if(CP.sk===\"t0\")return ordCmp(a,b)"):
            self.assertIn(fn, self.js)                                          # the flat tables (Parked, Charges) follow it by their Date column

    def test_the_controls_share_one_height(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertIn("#tsr select,#tsr .chip{box-sizing:border-box;height:32px", css)

    def test_recent_runs_not_yet_told_apart_refresh_by_themselves(self):
        self.assertIn("const kindWatch=()=>{", self.js)
        self.assertIn("(data.recent||[]).some(v=>!v.part)", self.js)      # only while a run has no parked / drive kind
        self.assertIn("kidle>=15", self.js)                               # and it stops asking when nothing new comes
        self.assertIn("loadKeep(!quiet);kindWatch()};", self.js)


if __name__ == "__main__":
    unittest.main()
