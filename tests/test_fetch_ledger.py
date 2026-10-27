import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
from fetch_ledger import FetchLedger  # noqa: E402


class LedgerTest(unittest.TestCase):
    def test_records_are_kept_and_only_finished_ones_are_settled(self):
        with tempfile.TemporaryDirectory() as d:
            L = FetchLedger(d)
            L.record("drive_path", "t1", "complete", "40 points")
            L.record("drive_path", "t2", "empty")
            L.record("drive_path", "t3", "failed", "HTTP 500")
            self.assertEqual(L.settled("drive_path"), {"t1", "t2"})
            self.assertEqual(FetchLedger(d).get("drive_path", "t3")["status"], "failed")
            self.assertIsNone(L.get("drive_path", "t9"))
            L.record("drive_path", "t3", "complete")
            self.assertEqual(FetchLedger(d).settled("drive_path"), {"t1", "t2", "t3"})

    def test_a_read_is_remembered_until_the_file_changes(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            L = FetchLedger(d)
            L.record("k", "a", "complete")
            first = L._load()
            self.assertIs(L._load(), first)                      # not read again
            self.assertIsNot(L._load(fresh=True), first)         # a writer gets its own copy
            L.record("k", "b", "empty")                          # a write is seen by the next read
            self.assertEqual(L.settled("k"), {"a", "b"})
            time.sleep(0.01)
            with open(L.path, "w") as f:                         # another program changed the file
                json.dump({"k": {"z": {"status": "complete"}}}, f)
            self.assertEqual(L.settled("k"), {"z"})


if __name__ == "__main__":
    unittest.main()
