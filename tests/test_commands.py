"""Every command behind Tools/tesla works from Tools/commands/ (2026-10-31 consolidation).

Checks: the tesla table and the commands folder hold the same scripts; every command's help comes up through ./tesla (and by its old
script name) quickly, without error and without writing anything; the one-off tools in maintenance/ show their help; the shell
scripts parse; a path a script works out from its own location points at Tools/ or the repo, not at commands/.
"""
import concurrent.futures
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOLS = os.path.join(REPO, "Tools")
CMDS = os.path.join(TOOLS, "commands")
MAINT = os.path.join(TOOLS, "maintenance")
TESLA = os.path.join(TOOLS, "tesla")


def _tesla_module():
    loader = importlib.machinery.SourceFileLoader("tesla_cli", TESLA)
    spec = importlib.util.spec_from_loader("tesla_cli", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def _run(args, cwd, timeout=90):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TESLA_DATA_DIR=os.path.join(cwd, "no-data"))
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


class CommandsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.T = _tesla_module()
        cls.table = [(g, c, s) for g, _t, cmds in cls.T.GROUPS for c, s, _w in cmds]

    def test_the_table_and_the_folder_hold_the_same_scripts(self):
        files = {f for f in os.listdir(CMDS) if f.endswith((".py", ".sh"))}
        mapped = [s for _g, _c, s in self.table]
        self.assertEqual(sorted(set(mapped)), sorted(files))
        self.assertEqual(len(mapped), len(set(mapped)), "a script is listed twice")
        names = [(g, c) for g, c, _s in self.table]
        self.assertEqual(len(names), len(set(names)), "a command name is used twice in a group")

    def test_every_command_shows_its_help_through_tesla_and_writes_nothing(self):
        work = tempfile.mkdtemp()

        def one(row):
            g, c, s = row
            r = _run([sys.executable, TESLA, g, c, "-h"], work)
            return row, r
        bad = []
        with concurrent.futures.ThreadPoolExecutor(6) as ex:
            for (g, c, s), r in ex.map(one, self.table):
                out = (r.stdout + r.stderr).strip()
                if r.returncode != 0 or not out or "Traceback" in out:
                    bad.append("%s %s (%s): rc=%s %s" % (g, c, s, r.returncode, out.splitlines()[-1] if out else "no output"))
        self.assertEqual(bad, [])
        self.assertEqual(os.listdir(work), [], "a command's help wrote files")

    def test_an_old_script_name_still_runs_and_a_wrong_name_says_so(self):
        work = tempfile.mkdtemp()
        r = _run([sys.executable, TESLA, "sync_api_tessie.py", "-h"], work)
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage", r.stdout.lower())
        r = _run([sys.executable, TESLA, "sync", "nothing-like-this"], work)
        self.assertEqual(r.returncode, 2)
        self.assertIn("No command", r.stdout)
        r = _run([sys.executable, TESLA], work)
        self.assertEqual(r.returncode, 0)
        for g, _t, _c in self.T.GROUPS:
            self.assertIn(g + " - ", r.stdout)

    def test_the_one_off_tools_show_their_help(self):
        work = tempfile.mkdtemp()
        bad = []
        for f in sorted(os.listdir(MAINT)):
            if f.endswith(".py"):
                r = _run([sys.executable, os.path.join(MAINT, f), "--help"], work)
                if r.returncode != 0 or "Traceback" in r.stderr:
                    bad.append("%s rc=%s %s" % (f, r.returncode, (r.stderr.strip().splitlines() or [""])[-1]))
        self.assertEqual(bad, [])

    def test_the_shell_scripts_parse(self):
        for d in (CMDS, MAINT, TOOLS):
            for f in os.listdir(d):
                if f.endswith(".sh"):
                    r = subprocess.run(["bash", "-n", os.path.join(d, f)], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, "%s: %s" % (f, r.stderr))

    def test_paths_worked_out_from_a_script_point_above_commands(self):
        one_up = re.compile(r"(?<!dirname\()os\.path\.dirname\(os\.path\.abspath\(__file__\)\)")
        bad = []
        for f in sorted(os.listdir(CMDS)):
            if not f.endswith(".py"):
                continue
            with open(os.path.join(CMDS, f), encoding="utf-8") as fh:
                lines = list(fh)
            for n, line in enumerate(lines, 1):
                if line.startswith("import os as _os, sys as _sys; _sys.path[:0]"):        # the path set-up: the commands folder itself is meant
                    continue
                if one_up.search(line):
                    bad.append("%s:%d" % (f, n))
        self.assertEqual(bad, [], "a path that stops at Tools/commands/ (one more os.path.dirname is needed)")

    def test_the_scripts_the_app_runs_are_there(self):
        app = open(os.path.join(TOOLS, "tesla_app.py"), encoding="utf-8").read()
        names = set(re.findall(r'ChargerSync\(CMDS, DATA_DIR, "([a-z_]+\.(?:py|sh))"', app)) | {"sync_charger_all.sh"}
        self.assertTrue({"sync_drives_all.sh", "sync_charging_all.sh", "analyse_charging.py"} <= names)
        for n in names:
            self.assertTrue(os.path.isfile(os.path.join(CMDS, n)), n)
        for lib_file, name in (("lib/drives/analyzer.py", "sync_api_tessie.py"), ("lib/footage_keeping.py", "sync_api_tessie.py")):
            self.assertIn('"commands", "%s"' % name, open(os.path.join(TOOLS, lib_file), encoding="utf-8").read())

    def test_every_script_can_be_run(self):
        bad = [f for d in (CMDS, MAINT) for f in os.listdir(d) if f.endswith((".py", ".sh")) and not os.access(os.path.join(d, f), os.X_OK)]
        self.assertEqual(bad, [], "missing u+x")

    def test_the_shared_paths_resolve(self):
        sys.path.insert(0, TOOLS)
        import lib.tessie_api_common as T
        self.assertEqual(os.path.realpath(T.REPO_ROOT), os.path.realpath(REPO))
        self.assertEqual(os.path.realpath(T.COMMANDS_DIR), os.path.realpath(CMDS))
        self.assertTrue(os.path.isfile(os.path.join(T.COMMANDS_DIR, "sync_api_tessie.py")))


if __name__ == "__main__":
    unittest.main()
