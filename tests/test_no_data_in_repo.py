"""No data is kept in the repository: the files the app writes for itself live in the data folder (directories.data)."""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = re.compile(r"^(teslacam_index_.*|sentry_index|footage_log.*|fetch_ledger|clip_clock|car_versions|tessie_.*_state)\.json$")


class NoDataInRepoTest(unittest.TestCase):
    def test_the_repository_root_holds_none_of_the_apps_data_files(self):
        found = sorted(f for f in os.listdir(ROOT) if DATA.match(f))
        self.assertEqual(found, [], "the app's data files belong in the data folder, not here: %s" % found)


if __name__ == "__main__":
    unittest.main()
