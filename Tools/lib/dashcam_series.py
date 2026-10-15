"""Per-frame telemetry from a Tesla dashcam clip (REQ-192).

Firmware 2025.44+ embeds a small protobuf (SEI NAL unit) in every video frame of a TeslaCam clip:
speed, gear, pedals, steering, blinkers, autopilot/FSD state, GPS and heading. read_series() walks the
MP4, decodes every one of them and returns plain lists (about 36 readings per second, ~2,200 per
1-minute clip). Pure Python, no dependencies. The video itself is not decoded here.

autopilot state (dashcam.proto AutopilotState): 0 none, 1 SELF_DRIVING (FSD), 2 AUTOSTEER, 3 TACC.
"""
import os
import struct

AP_NAMES = {0: "off", 1: "FSD", 2: "Autosteer", 3: "TACC"}


def _boxes(f, start, end):
    p = start
    while p < end:
        f.seek(p)
        h = f.read(8)
        if len(h) < 8:
            return
        size, typ = struct.unpack(">I4s", h)
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        elif size == 0:
            size = end - p
        if size < hdr:
            return
        yield typ.decode("latin1"), p + hdr, p + size
        p += size


def _varint(b, i):
    r = 0
    s = 0
    while i < len(b):
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            break
    return r, i


def _protobuf(b):
    i = 0
    o = {}
    while i < len(b):
        k, i = _varint(b, i)
        fn, wt = k >> 3, k & 7
        if fn == 0:
            break
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 1:
            v = struct.unpack("<d", b[i:i + 8])[0]
            i += 8
        elif wt == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 5:
            v = struct.unpack("<f", b[i:i + 4])[0]
            i += 4
        else:
            break
        o[fn] = v
    return o


def _movie_duration(f, start, end):
    """Duration in seconds from moov/mvhd, or None."""
    for typ, s, e in _boxes(f, start, end):
        if typ == "mvhd":
            f.seek(s)
            d = f.read(32)
            if d[0] == 1:
                ts = struct.unpack(">I", d[20:24])[0]
                du = struct.unpack(">Q", d[24:32])[0]
            else:
                ts = struct.unpack(">I", d[12:16])[0]
                du = struct.unpack(">I", d[16:20])[0]
            return du / ts if ts else None
    return None


def read_series(path):
    """Returns dict: n, dur (s), and parallel lists v (km/h), ap, br (0/1), lat, lon, hd, gear, steer, accel (pedal %), blk (indicator bits: 1 left, 2 right)."""
    S = {k: [] for k in ("v", "ap", "br", "lat", "lon", "hd", "gear", "steer", "accel", "blk")}
    dur = None
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        for typ, s, e in _boxes(f, 0, size):
            if typ == "moov":
                dur = _movie_duration(f, s, e)
            elif typ == "mdat":
                p = s
                while p + 5 <= e:
                    f.seek(p)
                    h = f.read(5)
                    if len(h) < 5:
                        break
                    ln = struct.unpack(">I", h[:4])[0]
                    if ln < 1 or p + 4 + ln > e:
                        break
                    if h[4] & 0x1F == 6:
                        nal = h[4:5] + f.read(ln - 1)
                        if len(nal) > 4 and nal[1] == 5:
                            i = 3
                            while i < len(nal) and nal[i] == 0x42:
                                i += 1
                            if i < len(nal) - 1 and nal[i] == 0x69:
                                try:
                                    m = _protobuf(nal[i + 1:-1].replace(b"\x00\x00\x03", b"\x00\x00"))
                                except Exception:
                                    m = {}
                                if m:
                                    S["v"].append(float(m.get(4, 0.0)) * 3.6)
                                    S["ap"].append(int(m.get(10, 0)))
                                    S["br"].append(1 if m.get(9) else 0)
                                    S["lat"].append(float(m.get(11, 0.0)))
                                    S["lon"].append(float(m.get(12, 0.0)))
                                    S["hd"].append(float(m.get(13, 0.0)))
                                    S["gear"].append(int(m.get(2, 0)))
                                    S["steer"].append(float(m.get(6, 0.0)))
                                    S["accel"].append(float(m.get(5, 0.0)))
                                    S["blk"].append((1 if m.get(7) else 0) + (2 if m.get(8) else 0))  # indicator: 1 left, 2 right, 3 both
                    p += 4 + ln
    n = len(S["v"])
    S["n"] = n
    S["dur"] = dur if dur else (n / 36.0 if n else 0.0)
    return S
