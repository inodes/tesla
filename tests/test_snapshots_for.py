"""Which drives need their snapshots built for a set of clips: only those that overlap a clip's minute and have none (or stale ones)."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))

import teslacam_sync as SY


def drive(data, day, name, start, end, snaps):
    f = os.path.join(data, "drives", "deepdive", day, name)
    os.makedirs(f)
    with open(os.path.join(f, "drive_summary.json"), "w") as fh:
        json.dump({"start": start, "end": end}, fh)
    if snaps:
        os.makedirs(os.path.join(f, "snapshots"))
        with open(os.path.join(f, "snapshots", "index.json"), "w") as fh:
            json.dump({"snapshots": [{"x": 1}]}, fh)
        os.utime(os.path.join(f, "snapshots", "index.json"), (4e9, 4e9))        # newer than the drive's files: not stale


class SnapshotsFor(unittest.TestCase):
    def test_only_drives_over_the_clips_without_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            drive(d, "2026-10-11", "a", "2026-10-11 14:40:00", "2026-10-11 14:50:00", False)       # covers the clip, no snapshots
            drive(d, "2026-10-11", "b", "2026-10-11 15:40:00", "2026-10-11 15:50:00", False)       # no clip over it
            drive(d, "2026-10-11", "c", "2026-10-11 14:41:00", "2026-10-11 14:49:00", True)        # covers the clip, built
            self.assertEqual(SY.snapshots_for(d, {"2026-10-11_14-45-00"}), ["a"])
            self.assertEqual(SY.snapshots_for(d, set()), [])


if __name__ == "__main__":
    unittest.main()
