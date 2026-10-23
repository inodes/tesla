#!/usr/bin/env python3
"""
archive_naming.py - Shared "XX_filename" archive-naming helper.
==================================================================
Archived raw CSVs used to be renamed "${file}.YYYYMMDDHHMM" - appending a
timestamp AFTER the extension. That defeats Spotlight/Finder's file-type
detection on macOS (the file no longer ends in .csv, so it doesn't show
up as one), and the exact minute isn't actually load-bearing - these are
landing-copy leftovers kept only so nothing consolidated is silently
deleted, not a version history anyone browses by time.

New scheme: "XX_${file}" - a zero-padded counter prefix, starting at
"00", incremented only as far as needed to avoid clobbering an existing
archived file of the same name. The original filename (and its
extension) stays intact at the end, so Spotlight/Finder still recognize
it correctly.
"""

import os


def next_archive_path(archive_dir, filename):
    """Full path to archive `filename` into `archive_dir` as
    `XX_filename`, where XX starts at "00" and increments only as needed
    to avoid overwriting an already-archived file of the same name.
    Caller is responsible for `os.makedirs(archive_dir, exist_ok=True)`
    beforehand (every call site already does this for its own reasons)."""
    n = 0
    while True:
        candidate = os.path.join(archive_dir, f"{n:02d}_{filename}")
        if not os.path.exists(candidate):
            return candidate
        n += 1
