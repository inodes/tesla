#!/usr/bin/env python3
"""test_docs_paths.py - the README and docs/*.md name only Tools/ paths and ./Tools/tesla commands that exist.

Every `Tools/...` path in README.md and docs/*.md must exist in the repo (a `*` or `<name>` in it is a pattern: at least
one file must match). Every `./Tools/tesla <group> <command>` must be a command in Tools/tesla's GROUPS, and every
`./Tools/tesla <script>.py|.sh` a script in Tools/commands/. A script run as `./<name>.py|.sh|.command` must be in the repo
root or in Tools/ (the README runs the app from Tools/), and a relative Markdown link must point at a file that exists.

Not checked: dated log lines (a bullet that starts with a date, `- 2026-10-31: ...`), which record what was true on that
day, and the few paths in EXCEPTIONS, each with its reason.

    python3 -m unittest tests/test_docs_paths.py
"""
import glob
import os
import re
import runpy
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# path as written in the doc -> why it is allowed to be missing
EXCEPTIONS = {
    "Tools/providers_chargers": "old folder name, named as moved to Tools/providers/chargers",
    "Tools/providers_power": "old folder name, named as moved to Tools/providers/power",
    "Tools/providers_pv": "old folder name, named as moved to Tools/providers/pv",
    "Tools/tessie_timezone.py": "docs/COMMANDS_AUDIT.md's 2026-10-19 snapshot, which says where it went",
    "Tools/lib/tessie_timezone.py": "docs/COMMANDS_AUDIT.md's 2026-10-19 snapshot, which says where it went",
}

PATH_RE = re.compile(r"(?<![\w/.~-])(?:\./)?(Tools/[\w.*/<>{}-]*[\w*>}/])")
TESLA_RE = re.compile(r"\./Tools/tesla[ \t]+([\w.-]+)(?:[ \t]+([\w-]+))?")
DATED_RE = re.compile(r"^\s*[-*] \d{4}-\d{2}-\d{2}\b")
DOT_SCRIPT_RE = re.compile(r"(?<![\w/.~-])\./([\w-]+\.(?:py|sh|command))\b")
LINK_RE = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def doc_files():
    return [os.path.join(REPO, "README.md")] + sorted(glob.glob(os.path.join(REPO, "docs", "*.md")))


def doc_lines(path):
    """(line number, text) of every line that is not a dated log entry."""
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not DATED_RE.match(line):
                yield n, line


def path_exists(p):
    p = p.rstrip("/")
    if any(c in p for c in "*<>{}"):
        pattern = re.sub(r"<[^>]*>|\{[^}]*\}", "*", p)
        return bool(glob.glob(os.path.join(REPO, pattern)))
    return os.path.exists(os.path.join(REPO, p))


def missing_paths(lines):
    """[(line number, path)] of Tools/ paths in these lines that do not exist and are not in EXCEPTIONS."""
    out = []
    for n, line in lines:
        for m in PATH_RE.finditer(line):
            p = m.group(1)
            if p.rstrip("/") not in EXCEPTIONS and p not in EXCEPTIONS and not path_exists(p):
                out.append((n, p))
    return out


def groups():
    ns = runpy.run_path(os.path.join(REPO, "Tools", "tesla"), run_name="tesla_listing")
    return {g: {c for c, _s, _w in cmds} for g, _t, cmds in ns["GROUPS"]}


def bad_commands(lines, known):
    """[(line number, command)] of ./Tools/tesla commands that do not exist. A first word that is neither a group nor a
    script name is prose ("`./Tools/tesla` lists them") and is skipped."""
    out = []
    for n, line in lines:
        for m in TESLA_RE.finditer(line):
            first, second = m.group(1), m.group(2)
            if first.endswith((".py", ".sh")):
                if not os.path.isfile(os.path.join(REPO, "Tools", "commands", first)):
                    out.append((n, "./Tools/tesla " + first))
            elif first in known and second and second not in known[first]:
                out.append((n, "./Tools/tesla %s %s" % (first, second)))
    return out


def missing_dot_scripts(lines):
    """[(line number, ./script)] of scripts run as ./<name> that are neither in the repo root nor in Tools/."""
    out = []
    for n, line in lines:
        for m in DOT_SCRIPT_RE.finditer(line):
            name = m.group(1)
            if not any(os.path.isfile(os.path.join(REPO, d, name)) for d in ("", "Tools")):
                out.append((n, "./" + name))
    return out


def missing_links(path, lines):
    """[(line number, target)] of relative Markdown links in this file that point at nothing."""
    out = []
    for n, line in lines:
        for m in LINK_RE.finditer(line):
            t = m.group(1)
            if re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):          # http:, https:, mailto:
                continue
            if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(path), t))):
                out.append((n, t))
    return out


class DocsPathsTest(unittest.TestCase):
    def test_tools_paths_exist(self):
        problems = []
        for f in doc_files():
            for n, p in missing_paths(doc_lines(f)):
                problems.append("%s:%d %s" % (os.path.relpath(f, REPO), n, p))
        self.assertEqual(problems, [], "docs name Tools/ paths that do not exist:\n" + "\n".join(problems))

    def test_tesla_commands_exist(self):
        known = groups()
        problems = []
        for f in doc_files():
            for n, c in bad_commands(doc_lines(f), known):
                problems.append("%s:%d %s" % (os.path.relpath(f, REPO), n, c))
        self.assertEqual(problems, [], "docs name ./Tools/tesla commands that do not exist:\n" + "\n".join(problems))

    def test_dot_scripts_exist(self):
        problems = []
        for f in doc_files():
            for n, c in missing_dot_scripts(doc_lines(f)):
                problems.append("%s:%d %s" % (os.path.relpath(f, REPO), n, c))
        self.assertEqual(problems, [], "docs run ./scripts that are not in the repo root or Tools/:\n" + "\n".join(problems))

    def test_links_exist(self):
        problems = []
        for f in doc_files():
            for n, t in missing_links(f, doc_lines(f)):
                problems.append("%s:%d %s" % (os.path.relpath(f, REPO), n, t))
        self.assertEqual(problems, [], "docs link to files that do not exist:\n" + "\n".join(problems))

    def test_exceptions_still_needed(self):
        """An exception for a path that now exists, or that no doc names any more, is removed."""
        named = set()
        for f in doc_files():
            for _n, line in doc_lines(f):
                named.update(m.group(1).rstrip("/") for m in PATH_RE.finditer(line))
        for p in EXCEPTIONS:
            self.assertFalse(path_exists(p), "%s exists now: drop it from EXCEPTIONS" % p)
            self.assertIn(p, named, "no doc names %s now: drop it from EXCEPTIONS" % p)

    def test_checker_finds_a_missing_path(self):
        lines = [(1, "Run `./Tools/no_such_script.py` or `Tools/lib/table_formatter.py`."),
                 (2, "- 2026-10-01: moved Tools/also_missing.py (dated, not checked)"),
                 (3, "See Tools/lib/*.py and Tools/commands/<name>.py, then /Volumes/*/Tools/ on the drive.")]
        kept = [(n, l) for n, l in lines if not DATED_RE.match(l)]
        self.assertEqual(missing_paths(kept), [(1, "Tools/no_such_script.py")])

    def test_checker_finds_a_bad_command(self):
        known = {"sync": {"drives"}}
        lines = [(1, "`./Tools/tesla sync drives` and `./Tools/tesla sync nothing`"),
                 (2, "`./Tools/tesla` lists them; `./Tools/tesla gone_script.py --x`")]
        self.assertEqual(bad_commands(lines, known),
                         [(1, "./Tools/tesla sync nothing"), (2, "./Tools/tesla gone_script.py")])


    def test_checker_finds_a_missing_script_and_link(self):
        lines = [(1, "`./tesla_app.py --browser` then `./drive_gps.py --budget 600`; see [x](Tessie/README.md), [y](LICENSE)"),
                 (2, "[web](https://example.com/a.md) `./Tools/tesla sync drives`")]
        self.assertEqual(missing_dot_scripts(lines), [(1, "./drive_gps.py")])
        self.assertEqual(missing_links(os.path.join(REPO, "README.md"), lines), [(1, "Tessie/README.md")])


if __name__ == "__main__":
    unittest.main()
