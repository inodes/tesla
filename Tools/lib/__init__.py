"""
Tools/lib/ - shared library modules for this repo's CLI scripts.

Not "providers" (Tools/providers_chargers/ - pluggable BaseChargerProvider
subclasses implementing one shared interface, swappable per charging
network) and not stand-alone CLI entry points (the *.py scripts one level
up in Tools/, each with its own argparse-based --help). Everything here is
a plain function/helper library that one or more CLI scripts import -
config/data-home path resolution, shared formatting, shared chart
rendering, timezone helpers, and similar cross-cutting plumbing with no
argparse or __main__ of its own (table_formatter.py's __main__ is a
self-test, not a CLI). See AGENTS.md Section 3 for the full repo layout
and the rule this package exists to enforce.
"""
