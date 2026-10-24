"""Tests for the TEMPORARY Tools/tmp_drives_csv_to_json.py (delete together with it)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import tmp_drives_csv_to_json as M  # noqa: E402
import drive_points as P  # noqa: E402


def points(t0, n=30):
    return [{"id": t0 + i * 10, "timestamp": t0 + i * 10, "state": "online", "shift_state": "D", "battery_level": 68, "latitude": -33.816 + i * 1e-4, "longitude": 151.0759,
             "elevation": 14, "speed": 40 + i % 5, "power": None, "odometer": 51.84 + i * 0.1, "locked": 1, "inside_temp": 18.6} for i in range(n)]


def csv_text(pts):
    lines = ['"' + '","'.join(["Timestamp (AEDT)"] + P.HEADINGS[1:]) + '"']
    for p in pts:
        row = [P.local_text(p["timestamp"], "Australia/Sydney")]
        for h, k in P.FIELDS[1:]:
            v = p.get(k)
            row.append("" if v is None else ("%.6f" % v if h in ("Latitude", "Longitude") else ("%.2f" % v if isinstance(v, float) else str(v))))
        lines.append('"' + '","'.join(row) + '"')
    return "\n".join(lines) + "\n"


class Rig:
    """A data folder with some CSV-only drives and a fake Tessie."""

    def __init__(self, d):
        self.data = d
        self.drives = {}                                   # from-epoch -> (stamp, points)
        self.calls = []
        self.script = {}                                   # from-epoch -> list of (status, body) to hand out first
        self.override = {}                                 # from-epoch -> function(points) -> points to answer with
        self.sleeps = []
        self.condensed = False                             # answer in Tessie's parallel-arrays form

    def add(self, day, hhmm, t0, n=30):
        stamp = "%s_%s_AEST" % (day, hhmm.replace(":", "-"))
        pts = points(t0, n)
        folder = os.path.join(self.data, "drives", "deepdive", day, stamp)
        os.makedirs(folder)
        cp = os.path.join(folder, "drive_deepdive_%s.csv" % stamp)
        with open(cp, "w") as fh:
            fh.write(csv_text(pts))
        os.utime(cp, (1780000000, 1780000000))
        self.drives[t0] = (stamp, pts)
        return stamp, cp

    def api(self, path, vin, token, params):
        t0 = params["from"]
        self.calls.append(dict(params))
        if self.script.get(t0):
            return self.script[t0].pop(0)
        stamp, pts = self.drives[t0]
        pts = self.override[t0](pts) if t0 in self.override else pts
        pts = [p for p in pts if params["from"] <= p["timestamp"] < params["to"]]      # Tessie's "to" is exclusive
        if self.condensed:
            keys = [k for k in pts[0] if k != "timestamp"]
            cols = {"ids": [p["timestamp"] for p in pts], "timestamps": [p["timestamp"] for p in pts]}
            cols.update({k + "s": [p.get(k) for p in pts] for k in keys})
            return 200, json.dumps({"results": cols}).encode()
        return 200, json.dumps({"results": pts}).encode()

    def run(self, *argv, creds=lambda: ("tok", "VIN")):
        args = M.parse(["--data-dir", self.data, "--pace", "0"] + list(argv))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = M.run(args, api=self.api, creds=creds, sleep=lambda s: self.sleeps.append(s), out=lambda t: print(t))
        return code, buf.getvalue()


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = Rig(self.tmp.name)
        self.good = [self.r.add("2026-06-01", "08:46", 1780267418), self.r.add("2026-06-02", "09:10", 1780360000), self.r.add("2026-06-03", "10:00", 1780450000)]

    def tearDown(self):
        self.tmp.cleanup()

    def arch(self, stamp):
        return os.path.join(self.tmp.name, M.ARCHIVE_SUB, stamp[:10], stamp, "drive_deepdive_%s.csv" % stamp)

    def test_the_code_check_passes_here(self):
        self.assertEqual(M.selfcheck(), [])

    def test_a_clean_run_fetches_compares_archives_and_says_all_done(self):
        code, out = self.r.run()
        self.assertEqual(code, 0, out)
        self.assertIn("ALL DONE", out)
        self.assertEqual(len(self.r.calls), 3)
        self.assertEqual({(c["format"], c["timezone"], c["interval"]) for c in self.r.calls}, {("json", "UTC", 1)})
        for stamp, cp in self.good:
            self.assertFalse(os.path.exists(cp), "the CSV was moved")
            self.assertTrue(os.path.isfile(self.arch(stamp)))
            jp = cp[:-4] + ".json"
            self.assertTrue(os.path.isfile(jp))
            self.assertEqual(os.path.getmtime(jp), 1780000000)                      # the drive's file date did not change
            self.assertEqual(len(P.epochs(jp)), 30)
        import drive_paths as DP
        self.assertEqual(len(DP.list_data(os.path.join(self.tmp.name, "drives", "deepdive"))), 3)

    def test_running_it_again_shows_the_status_and_asks_nobody(self):
        self.r.run()
        n = len(self.r.calls)
        code, out = self.r.run()
        self.assertEqual(len(self.r.calls), n)
        self.assertEqual(code, 0)
        self.assertIn("ALL DONE", out)
        self.assertIn("Step 4  CSV archived     3 of 3", out)
        code, out = self.r.run("--status", "--deep")
        self.assertIn("3 read back fine, 0 with a problem", out)

    def test_it_resumes_after_being_stopped_part_way(self):
        code, out = self.r.run("--limit", "1")
        self.assertEqual(len(self.r.calls), 1)
        self.assertEqual(code, 1)
        self.assertIn("Run it again to carry on", out)
        # a run killed in the middle: the third request raises, the state is still saved
        real = self.r.api
        seen = []

        def dying(path, vin, token, params):
            seen.append(params["from"])
            if len(seen) == 2:
                raise KeyboardInterrupt
            return real(path, vin, token, params)
        self.r.api = dying
        code, out = self.r.run()
        self.assertEqual(code, 1)
        self.assertIn("Stopped. Run again", out)
        self.r.api = real
        before = len(self.r.calls)
        code, out = self.r.run()
        self.assertEqual(code, 0, out)
        asked = [c["from"] for c in self.r.calls[before:]]
        self.assertEqual(len(asked), len(set(asked)))
        self.assertLessEqual(len(asked), 2)                                         # the one done in the first run is not asked again
        self.assertIn("ALL DONE", out)

    def test_a_json_already_on_disk_is_not_asked_for_again(self):
        stamp, cp = self.good[0]
        t0 = [k for k, v in self.r.drives.items() if v[0] == stamp][0]
        with open(cp[:-4] + ".json", "w") as fh:
            json.dump({"results": self.r.drives[t0][1]}, fh)                        # left by a run that was killed before it saved its state
        self.r.run()
        self.assertNotIn(t0, [c["from"] for c in self.r.calls])
        self.assertFalse(os.path.exists(cp))

    def test_rate_limits_and_dropped_connections_are_retried_with_growing_pauses(self):
        t0 = 1780267418
        ok = (200, json.dumps({"results": self.r.drives[t0][1]}).encode())
        self.r.script[t0] = [(429, b""), (None, b"timed out"), (503, b""), ok]
        code, out = self.r.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(len([c for c in self.r.calls if c["from"] == t0]), 4)
        w = self.r.sleeps[:3]
        self.assertGreaterEqual(w[0], 15)                                           # a 429 waits at least 15 s
        self.assertTrue(w[1] < w[2] or w[2] > 3, w)                                 # then the pauses grow
        self.assertTrue(all(x <= 90 for x in self.r.sleeps))

    def test_a_drive_that_will_not_answer_is_marked_failed_and_the_rest_carry_on(self):
        t0 = 1780360000
        self.r.script[t0] = [(500, b"")] * M.PAD_RETRIES
        code, out = self.r.run()
        self.assertEqual(code, 1)
        self.assertIn("failed", out)
        self.assertEqual(len([g for g in self.good if not os.path.exists(g[1])]), 2)    # the other two went all the way
        code, out = self.r.run()                                                    # next run asks for it again, and gets it
        self.assertEqual(code, 0, out)

    def rej(self, stamp):
        return os.path.join(self.tmp.name, M.REJECT_SUB, stamp[:10], stamp, "drive_deepdive_%s.json" % stamp)

    def test_a_wrong_json_is_backed_out_and_its_csv_stays_in_use(self):
        t0 = 1780360000
        self.r.override[t0] = lambda pts: [dict(p, speed=p["speed"] + 7) if i == 12 else p for i, p in enumerate(pts)]
        code, out = self.r.run()
        stamp, cp = self.good[1]
        jp = cp[:-4] + ".json"
        self.assertEqual(code, 1)
        self.assertTrue(os.path.isfile(cp), "a CSV that does not match is never moved")
        self.assertFalse(os.path.exists(jp), "the wrong JSON is no longer in the drive folder, so nothing reads it")
        self.assertTrue(os.path.isfile(self.rej(stamp)), "it is kept, not deleted")
        self.assertEqual(len(P.epochs(cp)), 30)                                       # the app reads the CSV for this drive
        import drive_paths as DP
        self.assertEqual(DP.find_data(os.path.dirname(os.path.dirname(os.path.dirname(cp))), stamp), cp)
        self.assertIn("backed out", out)
        self.assertIn("rejected", out)
        self.assertIn("Speed (km/h)", out)
        self.assertEqual([g for g in self.good if not os.path.exists(g[1])].__len__(), 2)      # the other two went all the way
        log = open(os.path.join(self.tmp.name, "drives", M.LOG_NAME), encoding="utf-8").read()
        self.assertIn("REJECT", log)

    def test_a_rejected_drive_is_not_asked_again_until_retry_and_then_can_pass(self):
        t0 = 1780360000
        self.r.override[t0] = lambda pts: [dict(p, speed=p["speed"] + 7) if i == 12 else p for i, p in enumerate(pts)]
        self.r.run()
        n = len(self.r.calls)
        self.r.run()
        self.assertEqual(len(self.r.calls), n)
        del self.r.override[t0]                                                       # Tessie gives the right answer this time
        code, out = self.r.run("--retry")
        stamp, cp = self.good[1]
        self.assertEqual(len(self.r.calls), n + 1)
        self.assertFalse(os.path.exists(cp))
        self.assertTrue(os.path.isfile(self.arch(stamp)))
        self.assertEqual(code, 0, out)

    def test_a_json_already_in_the_folder_that_differs_is_backed_out_too(self):
        stamp, cp = self.good[0]
        pts = self.r.drives[1780267418][1]
        jp = cp[:-4] + ".json"
        with open(jp, "w") as fh:
            json.dump({"results": [dict(p, speed=p["speed"] + 3) if i == 5 else p for i, p in enumerate(pts)]}, fh)
        code, out = self.r.run()
        self.assertFalse(os.path.exists(jp))
        self.assertTrue(os.path.isfile(self.rej(stamp)))
        self.assertTrue(os.path.isfile(cp))

    def test_only_the_first_row_differing_is_backed_out_unless_accept_edges(self):
        t0 = 1780360000
        self.r.override[t0] = lambda pts: [dict(p, latitude=p["latitude"] + 0.001) if i == 0 else p for i, p in enumerate(pts)]
        code, out = self.r.run()
        stamp, cp = self.good[1]
        self.assertTrue(os.path.isfile(cp))
        self.assertFalse(os.path.exists(cp[:-4] + ".json"))
        self.assertTrue(os.path.isfile(self.rej(stamp)))
        code, out = self.r.run("--retry", "--accept-edges")
        self.assertFalse(os.path.exists(cp))
        self.assertTrue(os.path.isfile(cp[:-4] + ".json"))
        self.assertEqual(code, 0, out)

    def test_extra_json_points_are_backed_out_unless_accept_extra(self):
        t0 = 1780360000
        self.r.override[t0] = lambda pts: pts + [dict(pts[-1], timestamp=pts[-1]["timestamp"] - 5, id=1)]    # a point inside the window the CSV does not have
        self.r.run()
        stamp, cp = self.good[1]
        self.assertTrue(os.path.isfile(cp))
        self.assertFalse(os.path.exists(cp[:-4] + ".json"))                           # backed out like any other discrepancy
        self.r.run("--retry", "--accept-extra")
        self.assertFalse(os.path.exists(cp))

    def test_an_empty_answer_is_settled_and_not_asked_again_until_retry(self):
        t0 = 1780450000
        self.r.script[t0] = [(200, b'{"results": []}')]
        code, out = self.r.run()
        self.assertIn("Tessie has none 1", out)
        n = len(self.r.calls)
        self.r.run()
        self.assertEqual(len(self.r.calls), n)
        self.r.run("--retry")
        self.assertEqual(len(self.r.calls), n + 1)

    def test_tessies_condensed_answer_is_read_compared_and_archived(self):
        self.r.condensed = True
        code, out = self.r.run()
        self.assertEqual(code, 0, out)
        self.assertIn("ALL DONE", out)
        for stamp, cp in self.good:
            self.assertFalse(os.path.exists(cp))
            self.assertEqual(len(P.epochs(cp[:-4] + ".json")), 30)

    def test_the_log_records_every_step_verbosely_and_never_the_token(self):
        t0 = 1780450000
        self.r.script[t0] = [(200, b'{"results": [], "note": "nothing here"}')]
        self.r.script[1780360000] = [(429, b"slow down")]
        self.r.run(creds=lambda: ("SECRET-TOKEN-123", "VIN"))
        lp = os.path.join(self.tmp.name, "drives", M.LOG_NAME)
        self.assertTrue(os.path.isfile(lp))
        txt = open(lp, encoding="utf-8").read()
        for word in ("RUN started", "RUN ended", "STATE", "REQUEST", " ok: 30 points", " EMPTY: ", "COMPARE", "ARCHIVE", "429"):
            self.assertIn(word, txt, word)
        self.assertIn("nothing here", txt)                                        # the whole empty answer is kept for diagnosis
        self.assertNotIn("SECRET-TOKEN-123", txt)
        n = txt.count("RUN started")
        self.r.run()
        self.assertEqual(open(lp, encoding="utf-8").read().count("RUN started"), n + 1)   # appended, not replaced

    def test_a_refused_token_stops_everything_and_changes_nothing(self):
        self.r.script[1780450000] = [(401, b"")]                                    # the newest drive is asked first
        code, out = self.r.run()
        self.assertEqual(code, 3)
        self.assertTrue(all(os.path.isfile(cp) for _, cp in self.good))
        self.assertEqual(len(self.r.calls), 1)

    def test_nothing_is_fetched_or_moved_when_the_code_check_fails(self):
        with mock.patch.object(M, "selfcheck", return_value=["JSON no longer wins"]):
            code, out = self.r.run()
        self.assertEqual(code, 2)
        self.assertIn("STOP", out)
        self.assertEqual(self.r.calls, [])

    def test_no_archive_keeps_every_csv_and_status_changes_nothing(self):
        self.r.run("--no-archive")
        self.assertTrue(all(os.path.isfile(cp) for _, cp in self.good))
        n = len(self.r.calls)
        snap = open(os.path.join(self.tmp.name, "drives", M.STATE_NAME)).read()
        code, out = self.r.run("--status")
        self.assertEqual(len(self.r.calls), n)
        self.assertEqual(snap.split('"code_check"')[0], open(os.path.join(self.tmp.name, "drives", M.STATE_NAME)).read().split('"code_check"')[0])
        self.assertIn("Step 2  Compare with CSV 3 compared: identical 3", out)

    def test_restore_puts_every_csv_back(self):
        self.r.run()
        self.r.run("--restore")
        self.assertTrue(all(os.path.isfile(cp) for _, cp in self.good))
        self.assertFalse(any(os.path.isfile(self.arch(s)) for s, _ in self.good))
        code, out = self.r.run()                                                    # and a full run moves them again, with no new requests
        self.assertEqual(len(self.r.calls), 3)
        self.assertIn("ALL DONE", out)

    def test_an_archived_drive_whose_json_goes_missing_is_reported(self):
        self.r.run()
        os.remove(self.good[0][1][:-4] + ".json")
        code, out = self.r.run("--status")
        self.assertEqual(code, 1)
        self.assertIn("its JSON is missing", out)


if __name__ == "__main__":
    unittest.main()
