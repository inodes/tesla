#!/usr/bin/env python3
"""test_event_roles_by_video.py - an event is on the drives that hold its VIDEO (the ledger), not on every drive that still has its folder (event.json, thumb.png stay behind when the minutes are moved),
and a parked minute with no telemetry takes the position and suburb of its event, so its label has the state ("Neutral Bay, NSW").
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_event_roles_by_video.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
import app_teslacam as AT  # noqa: E402


class RolesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tc = AT.TeslaCam(self.tmp.name)

    def test_a_drive_with_only_the_event_folder_is_not_a_holder(self):
        st = "2026-10-21_10-32-11"
        key = "sentry|2026-10-21_10-32-55|%s-front" % st
        led = AT.TLG.Ledger(self.tmp.name)
        led.mark("U", key, 100, True)
        led.mark("L", key, 100, True)
        led.mark("U", key, 100, False)                      # moved: the file is gone from U
        evs = [dict(id="2026-10-21_10-32-55", clips={st: ["front"]}, roles=["U", "L"])]
        self.assertEqual(self.tc._roles_by_video(evs)[0]["roles"], ["L"])

    def test_an_event_with_no_video_in_the_ledger_keeps_the_drives_that_list_it(self):
        evs = [dict(id="x", clips={"2026-10-21_10-32-11": ["front"]}, roles=["U"])]
        self.assertEqual(self.tc._roles_by_video(evs)[0]["roles"], ["U"])

    def test_an_event_places_file_gives_its_minutes_a_position_and_suburb(self):
        os.makedirs(os.path.join(self.tmp.name, "teslacam_events"))
        with open(os.path.join(self.tmp.name, "teslacam_events", "sentry_index.json"), "w") as f:
            json.dump({"e": dict(id="e", city="Neutral Bay", lat=-33.829, lon=151.22, clips={"2026-09-08_12-18-01": ["front"]})}, f)
        self.assertEqual(self.tc._event_places()["2026-09-08_12-18-01"], (-33.829, 151.22, "Neutral Bay"))


if __name__ == "__main__":
    unittest.main()
