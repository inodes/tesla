#!/usr/bin/env python3
"""test_command_help.py - every Python script in Tools/commands/ has NAME, SYNOPSIS and DESCRIPTION in its docstring.

AGENTS.md 3.3: the help of every command is a man page, and its module docstring is where a reader of the code
finds the same three sections. Each heading must be on a line of its own (flush left or indented), in the module
docstring, not in a later string. The docstring is read with ast, so no script is imported or run.

    python3 -m unittest tests/test_command_help.py
    ./tests/test_command_help.py
"""
import ast
import glob
import os
import re
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
COMMANDS = os.path.join(REPO, "Tools", "commands")
HEADINGS = ("NAME", "SYNOPSIS", "DESCRIPTION")


def missing_headings(path):
    """The headings the module docstring of this file lacks (all three when it has no docstring)."""
    with open(path, encoding="utf-8") as f:
        doc = ast.get_docstring(ast.parse(f.read(), filename=path), clean=False) or ""
    return [h for h in HEADINGS if not re.search(r"^[ \t]*%s[ \t]*$" % h, doc, re.M)]


class CommandDocstringTest(unittest.TestCase):
    def test_commands_folder_has_scripts(self):
        self.assertTrue(glob.glob(os.path.join(COMMANDS, "*.py")), "no .py files found in " + COMMANDS)

    def test_every_command_has_name_synopsis_description(self):
        lacking = {}
        for path in sorted(glob.glob(os.path.join(COMMANDS, "*.py"))):
            gaps = missing_headings(path)
            if gaps:
                lacking[os.path.relpath(path, REPO)] = gaps
        self.assertEqual(lacking, {}, "docstring lacks a heading: " +
                         "; ".join("%s: %s" % (p, ", ".join(g)) for p, g in lacking.items()))

    def test_checker_sees_a_missing_heading(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.py")
            with open(p, "w", encoding="utf-8") as f:
                f.write('"""x.py - a test.\n\nNAME\n    x.py - a test\n\nDESCRIPTION\n    SYNOPSIS is not a heading here.\n"""\n'
                        'X = """\nSYNOPSIS\n"""\n')
            self.assertEqual(missing_headings(p), ["SYNOPSIS"])
            with open(p, "w", encoding="utf-8") as f:
                f.write("X = 1\n")
            self.assertEqual(missing_headings(p), list(HEADINGS))


if __name__ == "__main__":
    unittest.main()
