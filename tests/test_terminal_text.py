#!/usr/bin/env python3
"""test_terminal_text.py - terminal table text (cell widths with colours, links and emoji, padding, cutting, UTC shown on the
local clock), the drive browser's selection parsing and footage-folder names, and the rule for a vehicle being in our lane.

Covers lib/table_formatter.py, lib/drives/browser.py (its pure helpers) and lib/traffic_finder.py (in_lane, find with the
detector faked, ensure_model with the download faked). Made-up text and boxes only, temp folders, no network. Runs without config or data:
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_terminal_text.py
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import table_formatter as TF  # noqa: E402
from lib.drives import browser as B  # noqa: E402
import traffic_finder as TR  # noqa: E402


class WidthTest(unittest.TestCase):
    def test_plain_wide_and_combining(self):
        self.assertEqual(TF.display_len("abc"), 3)
        self.assertEqual(TF.display_len("🚗"), 2)
        self.assertEqual(TF.display_len("漢字"), 4)
        self.assertEqual(TF.display_len("é"), 1)
        self.assertEqual(TF.display_len(""), 0)
        self.assertEqual(TF.display_len(None), 0)

    def test_colours_and_links_take_no_room(self):
        link = TF.hyperlink("-30.1, 140.2", "https://www.google.com/maps?q=-30.1,140.2")
        self.assertEqual(TF.strip_ansi(link), "-30.1, 140.2")
        self.assertEqual(TF.display_len(link), len("-30.1, 140.2"))
        self.assertEqual(TF.display_len(TF.bold_headers(["Date"])[0]), 4)

    def test_long_url_shortened_but_link_whole(self):
        url = "https://example.invalid/" + "x" * 80
        s = TF.format_display_url(url, 30)
        self.assertEqual(TF.strip_ansi(s), url[:27] + "...")
        self.assertIn(url, s)
        self.assertEqual(TF.format_display_url(""), "")

    def test_pad(self):
        self.assertEqual(TF.pad_display("ab", 5), "ab   ")
        self.assertEqual(TF.pad_display(12, 5, "right"), "   12")
        self.assertEqual(TF.pad_display("ab", 5, "center"), " ab  ")
        self.assertEqual(TF.pad_display("🚗x", 5), "🚗x  ")
        self.assertEqual(TF.pad_display("abcdefgh", 5), "abcdefgh")                     # longer: left alone unless asked
        self.assertEqual(TF.pad_display("abcdefgh", 5, truncate=True), "abcd…")

    def test_truncate(self):
        self.assertEqual(TF.truncate_display("abcdef", 6), "abcdef")
        self.assertEqual(TF.truncate_display("abcdef", 4), "abc…")
        self.assertEqual(TF.truncate_display("🚗🚗🚗", 4), "🚗…")
        self.assertEqual(TF.truncate_display("\033[1mabcdef\033[0m", 4), "\033[1mabc…\033[0m")    # colour kept and closed
        self.assertEqual(TF.truncate_display("abc", 0), "")

    def test_truncate_never_wider_than_asked(self):
        # Was a bug: target width was max(1, max_width - 1), so a 1-cell limit gave "a…", 2 cells.
        self.assertLessEqual(TF.display_len(TF.truncate_display("abc", 1)), 1)

    def test_truncate_width_limit_holds_for_every_width(self):
        self.assertEqual(TF.truncate_display("abc", 1), "…")
        self.assertEqual(TF.truncate_display("abcdef", 2, ellipsis="..."), "ab")     # ellipsis wider than the limit
        for text in ("abcdef", "🚗🚗🚗", "\033[1mabcdef\033[0m"):
            for w in range(1, 7):
                self.assertLessEqual(TF.display_len(TF.truncate_display(text, w)), w, (text, w))

    def test_row_and_lines(self):
        row = TF.format_row(["🚗 Drive", 12.5, TF.hyperlink("link", "https://example.invalid")], [10, 6, 6], ["left", "right", "center"])
        self.assertEqual(TF.display_len(row), 10 + 6 + 6 + 4)
        self.assertEqual(TF.strip_ansi(row), "│🚗 Drive  │  12.5│ link │")
        self.assertEqual(TF.format_row(["a"], [3, 2]), "│a  │  │")
        self.assertEqual(TF.format_box_line("┌", "┬", "┐", [2, 3]), "┌──┬───┐")
        self.assertEqual(TF.display_len(TF.format_title_line("Title", 20)), 22)

    def test_badge_and_field_line(self):
        # the label starts on the same cell after a 2-cell or a 1-cell icon
        self.assertEqual(TF.display_len(TF.format_type_badge("🔴 SC")) - 2, 4)
        self.assertEqual(TF.display_len(TF.format_type_badge("★ SC")) - 2, 4)
        self.assertEqual(TF.format_type_badge("Plain"), " Plain")
        self.assertEqual(TF.format_type_badge(""), "")
        line = TF.format_field_line("📍 ", "Where", "Here", bold_label=False)
        self.assertEqual(line, "  📍 Where:                 Here")

    def test_station_names(self):
        self.assertEqual(TF.clean_station_name("Testville  (Chargefox)  North", "chargefox"), "Testville North")
        self.assertEqual(TF.clean_station_name("A (B.C)", "B.C"), "A")
        self.assertEqual(TF.clean_station_name(None), "")
        self.assertEqual(TF.clean_station_short_name("Testville, NSW - (Main)"), "Testville_NSW_Main")

    def test_library_self_test_passes(self):
        self.assertTrue(TF.run_self_test(verbose=False))


class LocalTimestampTest(unittest.TestCase):
    """UTC text shown on the computer's own clock (the host's zone, daylight saving included)."""

    def _in_zone(self, zone):
        old = os.environ.get("TZ")
        os.environ["TZ"] = zone
        time.tzset()

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)

    def test_sydney_summer_and_winter(self):
        self._in_zone("Australia/Sydney")
        self.assertEqual(TF.format_local_timestamp("2026-01-15T01:05:00Z"), "2026-01-15 12:05 PM AEDT")
        self.assertEqual(TF.format_local_timestamp("2026-07-15T03:05:00Z"), "2026-07-15 1:05 PM AEST")
        self.assertEqual(TF.format_local_timestamp("2026-07-14T14:00:00+00:00", include_tz=False), "2026-07-15 12:00 AM")

    def test_adelaide_half_hour(self):
        self._in_zone("Australia/Adelaide")
        self.assertEqual(TF.format_local_timestamp("2026-01-15T01:05:00Z"), "2026-01-15 11:35 AM ACDT")

    def test_unreadable_kept_and_empty_blank(self):
        self.assertEqual(TF.format_local_timestamp("not a time"), "not a time")
        self.assertEqual(TF.format_local_timestamp(""), "")
        self.assertEqual(TF.format_local_timestamp(None), "")


class BrowserParsingTest(unittest.TestCase):
    def test_selection_indices(self):
        self.assertEqual(B.parse_selection_indices("1,5,8", 9), [1, 5, 8])
        self.assertEqual(B.parse_selection_indices("6-9", 7), [6, 7])
        self.assertEqual(B.parse_selection_indices("9-6", 9), [6, 7, 8, 9])
        self.assertEqual(B.parse_selection_indices(" 3 - 5 , 3", 9), [3, 4, 5])
        self.assertEqual(B.parse_selection_indices("a", 3), [1, 2, 3])
        self.assertEqual(B.parse_selection_indices("0,10,x,1-x", 9), [])
        self.assertEqual(B.parse_selection_indices("", 9), [])

    def test_range_selection(self):
        self.assertEqual(B.parse_range_selection("11", 20), [11])
        self.assertEqual(B.parse_range_selection("16-20", 18), [16, 17, 18])
        self.assertEqual(B.parse_range_selection("1,3 5", 9), [1, 3, 5])
        self.assertEqual(B.parse_range_selection(" ALL ", 3), [1, 2, 3])
        self.assertEqual(B.parse_range_selection("5-3", 9), [3, 4, 5])
        self.assertEqual(B.parse_range_selection("0,-1,99", 9), [])

    def test_range_with_spaces_is_a_range(self):
        # Was a bug: spaces were turned into commas before ranges were read, so "3 - 5" was 3 and 5 only (the batch
        # flag/unflag then touched two events, not three). parse_selection_indices reads the same text as 3, 4, 5.
        self.assertEqual(B.parse_range_selection("3 - 5", 9), [3, 4, 5])

    def test_range_spacing_variants(self):
        for text in ("3 -5", "3- 5", "3  -  5"):
            self.assertEqual(B.parse_range_selection(text, 9), [3, 4, 5], text)
        self.assertEqual(B.parse_range_selection("1, 3 - 5 8", 9), [1, 3, 4, 5, 8])
        self.assertEqual(B.parse_range_selection("1 3", 9), [1, 3])                  # a plain space still separates

    def test_event_slug(self):
        ev = {"start_dt": datetime(2026, 1, 15, 12, 5), "type": "drive", "drive": {"start_place": "Home Base", "end_place": "Shop/Mall #2"}}
        self.assertEqual(B.make_event_slug(ev), "2026-01-15_12-05_Drive_Home_Base_to_Shop_Mall_2")
        ev = {"start_dt": datetime(2026, 1, 15, 12, 5), "type": "idle", "location": "  Café  "}
        self.assertEqual(B.make_event_slug(ev), "2026-01-15_12-05_Parked_Caf")
        self.assertEqual(B.make_event_slug({"start_dt": datetime(2026, 1, 15, 0, 0)}), "2026-01-15_00-00_Parked_Parked")

    def test_wrap(self):
        self.assertEqual(B.wrap_text_display("short", 10), ["short"])
        self.assertEqual(B.wrap_text_display("alpha beta; gamma delta epsilon", 11), ["alpha beta", "gamma delta", "epsilon"])
        self.assertEqual(B.wrap_text_display("averyveryverylongword", 5), ["averyveryverylongword"])
        self.assertEqual(B.wrap_text_display("🚗🚗 🚗🚗", 4), ["🚗🚗", "🚗🚗"])


W = H = 1000


class LaneTest(unittest.TestCase):
    def test_in_lane_wedge(self):
        self.assertTrue(TR.in_lane(450, 800, 100, 150, W, H))             # bottom-middle at (500, 950)
        self.assertFalse(TR.in_lane(450, 300, 100, 200, W, H))            # bottom on the horizon
        # at the bottom row the wedge is 300 px either side of the middle
        self.assertTrue(TR.in_lane(799 - 10, 900, 20, 50, W, H))
        self.assertFalse(TR.in_lane(801 - 10, 900, 20, 50, W, H))
        # just under the horizon it is never narrower than 35 px either side
        self.assertTrue(TR.in_lane(534 - 5, 500, 10, 10, W, H))
        self.assertFalse(TR.in_lane(536 - 5, 500, 10, 10, W, H))

    def test_find_keeps_vehicles_ahead_nearest_first(self):
        boxes = [("car", 0.9, (480.0, 700.0, 40.0, 30.0)),        # in lane, 4 % wide
                 ("truck", 0.8, (420.0, 750.0, 160.0, 150.0)),    # in lane, 16 % wide: nearest
                 ("car", 0.9, (900.0, 800.0, 80.0, 100.0)),       # next lane
                 ("motorcycle", 0.7, (495.0, 600.0, 10.0, 20.0))]  # 1 % wide: too far to matter
        bgr = np.zeros((H, W, 3), np.uint8)
        with mock.patch.object(TR, "detect", return_value=boxes):
            out = TR.find(bgr, "unused.onnx")
        self.assertEqual([(d["kind"], d["near"], d["x"], d["w"]) for d in out], [("truck", 0.16, 420, 160), ("car", 0.04, 480, 40)])
        self.assertEqual(out[0]["crop"].shape, (150, 160, 3))

    def test_model_download(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "models", "m.onnx")

            class Resp:
                def __init__(self, n):
                    self.left = n

                def read(self, k):
                    n, self.left = min(k, self.left), self.left - min(k, self.left)
                    return b"\0" * n

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            self.assertFalse(TR.ensure_model(path, ""))
            with mock.patch.object(TR.urllib.request, "urlopen", return_value=Resp(1500000)) as uo:
                self.assertTrue(TR.ensure_model(path, "https://example.invalid/m.onnx"))
                self.assertTrue(TR.ensure_model(path, "https://example.invalid/m.onnx"))   # there now: no second download
            self.assertEqual(uo.call_count, 1)
            os.remove(path)
            with mock.patch.object(TR.urllib.request, "urlopen", side_effect=OSError("offline")):
                self.assertFalse(TR.ensure_model(path, "https://example.invalid/m.onnx"))
            self.assertEqual(os.listdir(os.path.dirname(path)), [])                          # no half file left
            with mock.patch.object(TR.urllib.request, "urlopen", return_value=Resp(1000)):
                self.assertFalse(TR.ensure_model(path, "https://example.invalid/m.onnx"))   # an error page is not a model


if __name__ == "__main__":
    unittest.main()
