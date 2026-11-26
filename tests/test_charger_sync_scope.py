"""The Sync chargers card follows the Network dropdown: one network's step (sync_charger_all.sh --only), the same names on the page and the server."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "Tools")
sys.path.insert(0, os.path.join(TOOLS, "lib"))
import app_sync  # noqa: E402

SCRIPT = os.path.join(TOOLS, "commands", "sync_charger_all.sh")


def stubbed():
    """The script beside a charger_find.py that only says what it was asked to do."""
    d = tempfile.mkdtemp()
    shutil.copy(SCRIPT, d)
    cf = os.path.join(d, "charger_find.py")
    with open(cf, "w") as f:
        f.write('#!/usr/bin/env bash\necho "RAN $*"\n')
    for n in ("charger_find.py", "sync_charger_all.sh"):
        os.chmod(os.path.join(d, n), os.stat(os.path.join(d, n)).st_mode | stat.S_IXUSR)
    return d


def run(d, *args):
    return subprocess.run(["bash", os.path.join(d, "sync_charger_all.sh"), *args], cwd=d, capture_output=True, text=True, timeout=60)


class ScriptTest(unittest.TestCase):
    def test_no_argument_runs_every_step(self):
        r = run(stubbed())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("RAN "), 12)

    def test_one_network_runs_its_step_and_the_export(self):
        r = run(stubbed(), "--only", "chargefox")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(re.findall(r"RAN (.*)", r.stdout), ["--sync-chargefox", "--export-consolidated"])

    def test_tesla_steps_are_told_apart(self):
        d = stubbed()
        self.assertIn("RAN --sync-tesla --sc", run(d, "--only", "tesla-sc").stdout)
        self.assertNotIn("--dc", run(d, "--only", "tesla-sc").stdout)
        self.assertIn("RAN --sync-tesla --dc", run(d, "--only", "tesla-dc").stdout)

    def test_an_unknown_network_is_refused(self):
        r = run(stubbed(), "--only", "bogus")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("RAN", r.stdout)

    def test_every_key_the_server_can_send_is_accepted_by_the_script(self):
        d = stubbed()
        for key in sorted(set(app_sync.CHARGER_NETS.values())):
            self.assertEqual(run(d, "--only", key).returncode, 0, key)


class MappingTest(unittest.TestCase):
    def test_names_map_to_steps(self):
        k = app_sync.charger_net_key
        self.assertEqual(k("Tesla"), "tesla-sc")
        self.assertEqual(k("Tesla Destination"), "tesla-dc")
        self.assertEqual(k("BP Pulse"), "bppulse")
        self.assertEqual(k("Evie Networks"), "evie")
        self.assertEqual(k("3rd-Party"), "plugshare")

    def test_a_small_network_came_in_through_plugshare(self):
        self.assertEqual(app_sync.charger_net_key("WeVolt"), "plugshare")

    def test_where_you_charged_is_not_a_network(self):
        for n in ("", "AC / home", "DC fast"):
            self.assertIn(n, app_sync.NOT_NETWORKS)

    def test_the_page_names_every_network_the_server_knows(self):
        with open(os.path.join(TOOLS, "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            js = f.read()
        block = js[js.index("const SYNCNET="):js.index("const SYNCNOT=")]
        for name in app_sync.CHARGER_NETS:
            self.assertIn('"%s"' % name, block, name)
        for name in app_sync.NOT_NETWORKS:
            self.assertIn('"%s"' % name, js[js.index("const SYNCNOT="):js.index("function syncAbout")], name)


class StartTest(unittest.TestCase):
    def test_start_passes_the_scope_to_the_script_and_reports_it(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "s.sh"), "w") as f:
            f.write('echo "got $*"\n')
        cs = app_sync.ChargerSync(d, d, "s.sh")
        cs.start(("--only", "jolt"))
        for _ in range(100):
            if not cs.running():
                break
            time.sleep(0.05)
        time.sleep(0.2)
        st = cs.status(0)
        self.assertEqual(st["args"], ["--only", "jolt"])
        self.assertIn("got --only jolt", st["lines"])
        cs.start()
        time.sleep(0.5)
        self.assertEqual(cs.status(0)["args"], [])


if __name__ == "__main__":
    unittest.main()
