"""teslacam_provenance.py - what a dashcam clip says about itself, read from the file header only (no video decoding, ~1 ms per clip).

The car writes the recording's start into the MP4 container as UTC (mvhd/tkhd/mdhd creation time) and the moment it closed the file as the
modification time. It also writes the street (comment) and suburb (title) it was in. These are the car's own facts: they do not depend on the
car's clock zone, on the file name, or on a copy's file-system time. The name stamp (YYYY-MM-DD_HH-MM-SS) is the car's CLOCK reading, which can
sit on the wrong offset, so the name only identifies a file (a label); every time, ordering and comparison uses the header UTC, never the name.

    read_header(path)  -> dict(created, modified, tracks_agree, street, suburb, size, error)   created/modified are UTC epoch seconds
    name_stamp(path)   -> naive epoch of the name stamp read as if UTC (the car's clock reading)
    offset(path)       -> car clock minus UTC in seconds (name stamp - header creation), the thing that jumps when the car's clock changes
"""
import calendar
import os
import re
import struct
import time

_MAC = 2082844800  # seconds between 1904-01-01 and 1970-01-01
_STAMP = re.compile(r"(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)")
_CONTAINERS = (b"moov", b"trak", b"mdia", b"udta", b"meta", b"ilst")
_TIMED = (b"mvhd", b"tkhd", b"mdhd")
_TAGS = {b"\xa9cmt": "street", b"\xa9nam": "suburb"}


def _walk(f, end, out, depth=0):
    while f.tell() + 8 <= end and depth < 8:
        p = f.tell()
        h = f.read(8)
        if len(h) < 8:
            return
        size, kind = struct.unpack(">I4s", h)
        hl = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hl = 16
        elif size == 0:
            size = end - p
        if size < hl:
            return
        if kind in _TIMED:
            b = f.read(min(size - hl, 32))
            if len(b) >= 20:
                c, m = struct.unpack(">II", b[4:12]) if b[0] == 0 else struct.unpack(">QQ", b[4:20])
                out["times"].append((c, m))
        elif kind in _TAGS:
            b = f.read(size - hl)
            i = b.find(b"data")
            if i >= 0:
                out[_TAGS[kind]] = b[i + 12:].decode("utf-8", "replace").strip("\x00 ")
        elif kind in _CONTAINERS:
            if kind == b"meta":
                f.seek(4, 1)  # version/flags
            _walk(f, p + size, out, depth + 1)
        f.seek(p + size)


def read_header(path):
    r = dict(created=None, modified=None, tracks_agree=None, street="", suburb="", size=None, error="")
    try:
        r["size"] = os.path.getsize(path)
        out = dict(times=[])
        with open(path, "rb") as f:
            _walk(f, r["size"], out)
        t = [(c - _MAC, m - _MAC) for c, m in out["times"] if c]
        if not t:
            r["error"] = "no creation time in header"
            return r
        r["created"], r["modified"] = t[0]
        r["tracks_agree"] = all(x == t[0] for x in t)
        r["street"], r["suburb"] = out.get("street", ""), out.get("suburb", "")
    except Exception as e:
        r["error"] = "%s: %s" % (type(e).__name__, e)
    return r


def name_stamp(path):
    m = _STAMP.search(os.path.basename(path))
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups())
    return calendar.timegm((y, mo, d, h, mi, s))


def offset(path, header=None):
    h = header or read_header(path)
    n = name_stamp(path)
    if h["created"] is None or n is None:
        return None
    return n - h["created"]
