"""test_config_in_data_folder.py - the full config.json is read from the data folder when it is there; the repo's config.json only has to say where that is.

    python3 -m unittest tests/test_config_in_data_folder.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import tessie_api_common as T  # noqa: E402


class DataConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.keep = {k: os.environ.pop(k, None) for k in ("TESLA_CONFIG", "TESLA_DATA_DIR")}
        self.root = (T.ROOT_CONFIG_PATH, T._LEGACY_TESSIE_CONFIG_PATH)
        T.ROOT_CONFIG_PATH = os.path.join(self.tmp, "repo_config.json")
        T._LEGACY_TESSIE_CONFIG_PATH = os.path.join(self.tmp, "none.json")

    def tearDown(self):
        T.ROOT_CONFIG_PATH, T._LEGACY_TESSIE_CONFIG_PATH = self.root
        for k, v in self.keep.items():
            if v is not None:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_data_folders_config_wins_and_the_repo_file_only_names_the_folder(self):
        data = os.path.join(self.tmp, "data")
        os.makedirs(data)
        open(os.path.join(data, "config.json"), "w").write("{}")
        json.dump({"directories": {"data": data}}, open(T.ROOT_CONFIG_PATH, "w"))
        self.assertEqual(T.resolve_config_path(), os.path.join(data, "config.json"))

    def test_without_one_in_the_data_folder_the_repo_file_is_used(self):
        data = os.path.join(self.tmp, "data")
        os.makedirs(data)
        json.dump({"directories": {"data": data}}, open(T.ROOT_CONFIG_PATH, "w"))
        self.assertEqual(T.resolve_config_path(), T.ROOT_CONFIG_PATH)

    def test_the_environment_names_the_data_folder_and_a_config_file_beats_both(self):
        data = os.path.join(self.tmp, "d2")
        os.makedirs(data)
        open(os.path.join(data, "config.json"), "w").write("{}")
        os.environ["TESLA_DATA_DIR"] = data
        self.assertEqual(T.resolve_config_path(), os.path.join(data, "config.json"))
        os.environ["TESLA_CONFIG"] = os.path.join(self.tmp, "x.json")
        self.assertEqual(T.resolve_config_path(), os.path.join(self.tmp, "x.json"))
        del os.environ["TESLA_CONFIG"]
        del os.environ["TESLA_DATA_DIR"]


if __name__ == "__main__":
    unittest.main()
