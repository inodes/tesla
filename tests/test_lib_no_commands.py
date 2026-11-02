#!/usr/bin/env python3
"""test_lib_no_commands.py - shared code lives in Tools/lib, not in the command scripts.

Nothing in Tools/lib, the provider packages or Tools/tesla_app.py imports a script from Tools/commands, and the three names that are both a
lib module and a command (teslacam_ledger, teslacam_scan, teslacam_stills) are imported as lib.<name> everywhere, so a plain import can never
pick up the command file when Tools/commands comes first on the path.

    python3 -m unittest tests/test_lib_no_commands.py
"""
import ast
import glob
import os
import subprocess
import sys
import unittest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools"))
COMMANDS = os.path.join(TOOLS, "commands")
CMD_NAMES = {os.path.splitext(f)[0] for f in os.listdir(COMMANDS) if f.endswith(".py")}
LIB_NAMES = {os.path.splitext(f)[0] for f in os.listdir(os.path.join(TOOLS, "lib")) if f.endswith(".py")}
BOTH = CMD_NAMES & LIB_NAMES


def imports(path):
    """[(line, module)] of every absolute import in a file, at any depth (function-level imports too)."""
    out = []
    for n in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(n, ast.Import):
            out += [(n.lineno, a.name) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append((n.lineno, n.module))
    return out


def shared_code_files():
    files = glob.glob(os.path.join(TOOLS, "lib", "**", "*.py"), recursive=True)
    for pkg in glob.glob(os.path.join(TOOLS, "providers_*")):
        files += glob.glob(os.path.join(pkg, "**", "*.py"), recursive=True)
    return files + [os.path.join(TOOLS, "tesla_app.py")]


class LibDoesNotImportCommands(unittest.TestCase):
    def test_no_command_imports(self):
        bad = []
        for f in shared_code_files():
            for line, mod in imports(f):
                top = mod.split(".")[0]
                if top == "commands" or top in CMD_NAMES:
                    bad.append("%s:%d imports %s" % (os.path.relpath(f, TOOLS), line, mod))
        self.assertEqual(bad, [], "shared code imports a command script; move what it needs into Tools/lib")

    def test_shared_names_imported_from_lib(self):
        self.assertTrue(BOTH, "expected the teslacam_* names in both folders")
        files = glob.glob(os.path.join(TOOLS, "**", "*.py"), recursive=True) + glob.glob(os.path.join(TOOLS, "..", "tests", "*.py"))
        bad = []
        for f in files:
            if os.sep + "archive" + os.sep in f:
                continue
            for line, mod in imports(f):
                if mod in BOTH:
                    bad.append("%s:%d imports %s (use: from lib import %s)" % (os.path.relpath(f, TOOLS), line, mod, mod))
        self.assertEqual(bad, [])

    def test_app_loads_nothing_from_commands(self):
        code = ("import io, os, sys, contextlib\n"
                "sys.path.insert(0, %r)\n"
                "with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):\n"
                "    import tesla_app, lib.charging_analyzer, lib.places_common, lib.footage_actions, lib.ev_efficiency, lib.session_cost\n"
                "c = %r\n"
                "print([k for k, m in sys.modules.items() if (getattr(m, '__file__', None) or '').startswith(c + os.sep)])\n"
                "print(c in sys.path)\n") % (TOOLS, COMMANDS)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=TOOLS)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        loaded, on_path = r.stdout.strip().splitlines()[-2:]
        self.assertEqual(loaded, "[]")
        self.assertEqual(on_path, "False")


if __name__ == "__main__":
    unittest.main()
