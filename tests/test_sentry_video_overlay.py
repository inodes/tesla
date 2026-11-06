#!/usr/bin/env python3
"""test_sentry_video_overlay.py - the video of a Sentry event carries the place and the time over it, and the camera that detected the event is marked (also when the event
is picked inside a park's video).

    python3 -m unittest tests/test_sentry_video_overlay.py
"""
import os
import sys
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_teslacam as AT  # noqa: E402
import clip_clock as CC  # noqa: E402


def src(*p):
    with open(os.path.join(HERE, "..", *p), encoding="utf-8") as f:
        return f.read()


class WhereEvents(unittest.TestCase):
    def fake(self, label="West Ryde, NSW"):
        calls = []
        me = SimpleNamespace(_short_where=lambda store, st: (calls.append(st), label)[1], _tl=lambda r: None)
        me.__dict__["_ev_at"] = {}
        return me, calls

    def test_an_event_gets_the_short_place_of_the_minute_it_happened_in(self):
        me, calls = self.fake()
        ev = dict(id="2026-11-03_11-03-18", t=CC.utc("2026-11-03_11-03-18"), clips={"2026-11-03_11-02-30": ["front"], "2026-11-03_11-03-30": ["front"]})
        out = AT.TeslaCam.where_events(me, [ev])
        self.assertEqual(out[0]["at"], "West Ryde, NSW")
        self.assertEqual(calls, ["2026-11-03_11-02-30"])            # the minute that was recording at the moment of the event, not the one after it

    def test_found_once_and_a_missing_place_is_looked_for_again(self):
        me, calls = self.fake("")
        ev = dict(id="x", t=1, clips={"2026-11-03_11-02-30": ["front"]})
        AT.TeslaCam.where_events(me, [ev])
        AT.TeslaCam.where_events(me, [ev])
        self.assertEqual(len(calls), 2)
        me2, calls2 = self.fake()
        AT.TeslaCam.where_events(me2, [ev])
        AT.TeslaCam.where_events(me2, [ev])
        self.assertEqual(len(calls2), 1)


class Ui(unittest.TestCase):
    def setUp(self):
        self.js = src("Tools", "lib", "app_ui", "app.js")

    def test_the_event_carries_its_clock_offset(self):
        self.assertIn("loc=loc, lo=lo)", src("Tools", "lib", "app_sentry.py"))

    def test_the_overlay_goes_on_the_camera_shown_and_the_sentry_players_have_it(self):
        self.assertIn("if(!(o.tele||o.info)||!mv)return;", self.js)
        self.assertIn("figs.find(shown)", self.js)                      # not only the front camera
        self.assertIn("const fr=!!o.tele&&fig.dataset.c===\"front\"", self.js)     # the sign, speed and indicators stay on the front one
        self.assertEqual(self.js.count("info:evInfo(()=>"), 2)                      # the event page and the player of a Saved / Sentry event
        self.assertIn("function evInfo(loc,at,lo)", self.js)

    def test_the_detecting_camera_is_marked_and_follows_the_event_picked(self):
        self.assertIn('${o.hot===c?" trg":""}', self.js)                           # its button
        self.assertIn("setEvent:(cam,trig)=>", self.js)
        self.assertIn('player.setEvent(sn?EVCAM[v.cam]||"front":""', self.js)
        self.assertIn(".chip.trg", src("Tools", "lib", "app_ui", "app.css"))


class OnePage(unittest.TestCase):
    """A Sentry detection opens the page of the drive or park that holds it, with the event picked: no page of its own beside it."""

    def setUp(self):
        self.js = src("Tools", "lib", "app_ui", "app.js")

    def test_the_event_page_hands_over_to_the_row_that_holds_it(self):
        i = self.js.index("async function pageTeslaCamSentry(")
        blk = self.js[i:i + 4500]
        self.assertIn("const h=await rowHashAt(e.t,e.id);if(h){NAVREPL=true;location.replace(", blk)
        self.assertLess(blk.index("rowHashAt(e.t,e.id)"), blk.index("sentryWindow(role,e,15)"))

    def test_the_row_address_carries_the_event_and_the_page_picks_it(self):
        self.assertIn("async function rowHashAt(u,ev)", self.js)
        self.assertIn("async function goMoment(u,ev)", self.js)
        self.assertIn("([w,tid,ev])=>", self.js)                                   # win-<a>-<b>-<lo>-<kind>~<tid>~<event>
        self.assertIn("async function pageTeslaCamAt(t,t1,lo,k,tid,ev)", self.js)
        self.assertIn("{row,ev})", self.js)
        self.assertIn("if(opts.ev){const k=d.events.findIndex(v=>v.id===opts.ev);opts.ev=null;if(k>=0)pickEv(k,true)}", self.js)

    def test_a_card_and_an_opened_event_share_one_pick(self):
        self.assertEqual(self.js.count("const pickEv="), 1)
        self.assertIn('$$("#dev .evc").forEach((b,k)=>b.onclick=()=>pickEv(k));', self.js)
        self.assertIn('x.classList.toggle("on",j===k)', self.js)                   # the picked card is marked


class NothingDroppedForStartingNear(unittest.TestCase):
    """The Sentry minute of 6 Nov 2026 11:05:19 (13.5 s long, closed when Recent 11:05:33 began) is kept: no clip is dropped for starting near another."""

    def test_the_minutes_of_an_event_beside_a_recent_run_are_all_kept(self):
        me = SimpleNamespace(_sources=lambda role, tc: ({"2026-11-06_11-05-33": ["front"]}, [], [dict(id="ev", clips={"2026-11-06_11-04-18": ["front"], "2026-11-06_11-05-19": ["front"]})]))
        got = [m["stamp"] for m in AT.TeslaCam._sentry_minutes(me, "A", "tc", "ev")]
        self.assertEqual(got, ["2026-11-06_11-04-18", "2026-11-06_11-05-19"])


class PlaceOnTheLabel(unittest.TestCase):
    def test_a_minute_without_a_place_carries_the_parks_place(self):
        js = src("Tools", "lib", "app_ui", "app.js")
        self.assertIn("return{a:m.at||parkPlace(),", js)
        self.assertIn("parkedAt(nameAt(places,x.p0)||nameAt(places,x.p1)||\"\")", js)      # the heading's own rule


if __name__ == "__main__":
    unittest.main()
