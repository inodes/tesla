"""
Tools/lib/ - shared library modules for this repo's CLI scripts.

Not "providers" (Tools/providers/ - e.g. pluggable BaseChargerProvider
subclasses implementing one shared interface, swappable per charging
network) and not stand-alone CLI entry points (the *.py scripts in
Tools/commands/, each with its own argparse-based --help). Nothing here
imports from Tools/commands/ (tests/test_lib_no_commands.py). Everything here is
a plain function/helper library that one or more CLI scripts import -
config/data-home path resolution, shared formatting, shared chart
rendering, timezone helpers, and similar cross-cutting plumbing with no
argparse or __main__ of its own (table_formatter.py's __main__ is a
self-test, not a CLI). See AGENTS.md Section 3 for the full repo layout
and the rule this package exists to enforce.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:          # a module in here imports another by its plain name (import tessie_api_common): found here, whatever folder started the program
    _sys.path.append(_HERE)
