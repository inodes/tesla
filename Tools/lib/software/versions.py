"""versions.py - Tesla software version strings ("2026.38.5.5"): numbers, order, line.

    parse(v)   -> tuple of ints, or None when v is not numbers separated by dots
    key(v)     -> a tuple that sorts versions in release order (padded, so "2026.38" == "2026.38.0")
    sortkey(v) -> the same as a text that sorts in a database ("2026.0038.0005...")
    line(v)    -> "2026.38": the year and week the build was cut (the branch a point release sits on)
    is_year(v) -> True for 2019.x-style versions (older "4.10.3050" ones are not)
Number order is release order, NOT ancestry: 2026.38.3.1 and 2026.38.5.5 are different builds that can be on different cars at the same time.
"""
import re

_NUM = re.compile(r"^\d+(?:\.\d+)*$")
WIDTH = 6


def parse(v):
    v = (v or "").strip()
    return tuple(int(x) for x in v.split(".")) if _NUM.match(v) else None


def key(v):
    p = parse(v)
    if p is None:
        return (-1,) * WIDTH
    return (p + (0,) * WIDTH)[:WIDTH] if len(p) <= WIDTH else p[:WIDTH]


def sortkey(v):
    return ".".join("%05d" % max(n, 0) for n in key(v))


def line(v):
    p = parse(v)
    return "%d.%d" % p[:2] if p and len(p) >= 2 else (v or "")


def is_year(v):
    p = parse(v)
    return bool(p and p[0] >= 2012)
