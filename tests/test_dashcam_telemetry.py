#!/usr/bin/env python3
"""test_dashcam_telemetry.py - reading the per-frame telemetry Tesla writes into a dashcam clip (lib/dashcam_series.py's MP4
walk and protobuf decode, lib/sei_inspector.py's SEI payload reader), and a drive's own times and rows for its footage
(lib/drive_footage.py: UTC epochs turned into Sydney local time across daylight saving, the master row nearest a time,
the drive's window, power from pack current and voltage).

The clips are tiny made-up MP4 files built here (a moov/mvhd box and an mdat of NAL units); no real footage is read.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_dashcam_telemetry.py
"""
import datetime
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import dashcam_series as DS  # noqa: E402
from lib import sei_inspector as SEI  # noqa: E402
from lib import drive_footage as DFT  # noqa: E402


# ------------------------------------------------------------------------------------------------------------ building a clip
def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field(fn, value, kind):
    if kind == "varint":
        return varint(fn << 3 | 0) + varint(value)
    if kind == "double":
        return varint(fn << 3 | 1) + struct.pack("<d", value)
    if kind == "float":
        return varint(fn << 3 | 5) + struct.pack("<f", value)
    raise ValueError(kind)


def reading(speed_ms=25.0, ap=1, brake=0, lat=-33.5, lon=150.0, heading=90.0, gear=1, steer=-3.5, accel=12.0, left=0, right=1):
    """One frame's SeiMetadata protobuf, field numbers as in dashcam.proto."""
    return (field(1, 1, "varint") + field(2, gear, "varint") + field(4, speed_ms, "float") + field(5, accel, "float")
            + field(6, steer, "float") + field(7, left, "varint") + field(8, right, "varint") + field(9, brake, "varint")
            + field(10, ap, "varint") + field(11, lat, "double") + field(12, lon, "double") + field(13, heading, "double"))


def emulation_prevent(b):
    """H.264 emulation prevention: 00 00 followed by 00..03 gets a 03 put in between."""
    out, zeros = bytearray(), 0
    for c in b:
        if zeros >= 2 and c <= 3:
            out.append(3)
            zeros = 0
        out.append(c)
        zeros = zeros + 1 if c == 0 else 0
    return bytes(out)


def sei_nal(proto):
    """An SEI NAL as the car writes it: type 6, payload type 5, size, 0x42 padding, 0x69, the protobuf, the stop bit."""
    body = bytes([0x42, 0x42, 0x42, 0x69]) + proto
    return bytes([0x06, 0x05, min(255, len(body))]) + emulation_prevent(body) + b"\x80"


def box(typ, payload, large=False):
    if large:
        return struct.pack(">I4sQ", 1, typ, 16 + len(payload)) + payload
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def mvhd(timescale, duration):
    return box(b"mvhd", bytes(4) + bytes(8) + struct.pack(">II", timescale, duration) + bytes(80))


def clip(nals, timescale=1000, duration=60000, large_mdat=False, with_moov=True):
    mdat = b"".join(struct.pack(">I", len(n)) + n for n in nals)
    return box(b"ftyp", b"isom" + bytes(4)) + (box(b"moov", mvhd(timescale, duration)) if with_moov else b"") + box(b"mdat", mdat, large=large_mdat)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_dashcam_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb" if isinstance(data, bytes) else "w") as f:
            f.write(data)
        return p


# ------------------------------------------------------------------------------------------------------------ dashcam_series
class SeriesTest(TempDirTest):
    def test_protobuf_and_varint(self):
        self.assertEqual(DS._varint(varint(300), 0), (300, 2))
        self.assertEqual(DS._protobuf(field(2, 3, "varint") + field(11, -33.5, "double")), {2: 3, 11: -33.5})
        self.assertEqual(DS._protobuf(b"\x00\x05"), {})                     # field 0 ends it

    def test_reading_a_clip(self):
        slice_nal = bytes([0x65, 0x88, 0x84, 0x00])                       # a video slice: not telemetry
        other_sei = bytes([0x06, 0x04, 0x02, 0x00, 0x00, 0x80])           # an SEI of another payload type
        nals = [sei_nal(reading()), slice_nal, other_sei,
                sei_nal(reading(speed_ms=0.0, ap=3, brake=1, lat=-33.25, lon=150.25, gear=0, left=1, right=1))]
        s = DS.read_series(self.write("2026-11-01_10-00-00-front.mp4", clip(nals)))
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["dur"], 60.0)
        self.assertAlmostEqual(s["v"][0], 90.0)                           # 25 m/s
        self.assertEqual((s["ap"], s["br"], s["gear"], s["blk"]), ([1, 3], [0, 1], [1, 0], [2, 3]))
        self.assertEqual((s["lat"], s["lon"], s["hd"]), ([-33.5, -33.25], [150.0, 150.25], [90.0, 90.0]))
        self.assertAlmostEqual(s["steer"][0], -3.5)
        self.assertAlmostEqual(s["accel"][0], 12.0)
        self.assertEqual(DS.AP_NAMES[s["ap"][0]], "FSD")

    def test_zero_bytes_survive_emulation_prevention(self):
        # 0.0 as a double is eight zero bytes: the car's encoder puts 03s in, the reader must take them out again
        s = DS.read_series(self.write("c.mp4", clip([sei_nal(reading(lat=0.0, lon=0.0, heading=0.0, steer=0.0))])))
        self.assertEqual((s["lat"], s["lon"], s["hd"], s["steer"]), ([0.0], [0.0], [0.0], [0.0]))

    def test_large_mdat_and_no_moov(self):
        s = DS.read_series(self.write("c.mp4", clip([sei_nal(reading())] * 72, large_mdat=True, with_moov=False)))
        self.assertEqual((s["n"], s["dur"]), (72, 2.0))                    # no mvhd: 36 readings a second
        s = DS.read_series(self.write("e.mp4", clip([])))
        self.assertEqual((s["n"], s["dur"]), (0, 60.0))

    def test_a_cut_off_file_stops_cleanly(self):
        data = clip([sei_nal(reading())] * 3)
        s = DS.read_series(self.write("cut.mp4", data[:-20]))
        self.assertEqual(s["n"], 2)                                       # the frames written before the cut are kept
        self.assertEqual(s["dur"], 60.0)


# ------------------------------------------------------------------------------------------------------------ sei_inspector
class SeiInspectorTest(unittest.TestCase):
    def test_payloads_after_emulation_prevention(self):
        uuid = bytes(range(16))
        raw = bytes([5, 20]) + uuid + b"\x00\x00\x01\x02" + bytes([4, 6]) + b"\xb5\x00\x31\x47AB" + bytes([1, 2]) + b"xy"
        got = SEI._parse_sei_rbsp(emulation_prevent(raw))
        self.assertEqual(got, [b"\x00\x00\x01\x02", b"AB", b"xy"])       # UUID and T.35 header dropped
        self.assertEqual(SEI._parse_sei_rbsp(bytes([5, 50, 1, 2])), [])   # a size past the end is not read

    def test_decode(self):
        t = SEI._decode_sei_telemetry(reading(gear=2, brake=1))
        self.assertEqual((t["gear"], t["speed_kmh"], t["speed_mph"], t["brake_applied"]), ("REVERSE", 90.0, 55.9, True))
        self.assertEqual((t["latitude"], t["longitude"], t["blinker_left"], t["blinker_right"]), (-33.5, 150.0, False, True))
        self.assertEqual((t["steering_deg"], t["accelerator_pct"]), (-3.5, 12.0))
        self.assertIsNone(SEI._decode_sei_telemetry(field(2, 1, "varint")))   # no version field: not telemetry
        self.assertEqual(SEI._parse_protobuf(field(11, 1.0, "double")[:-2]), {})  # cut short: stops

    @unittest.expectedFailure
    def test_autopilot_state_names_match_dashcam_proto(self):
        """BUG (found by this test): sei_inspector names AutopilotState 1 "Available" and 3 "Active / Engaged" (autopilot_active
        only for 3), but dashcam.proto - as lib/dashcam_series.py and lib/app_drive_review.py both read it - has 0 none,
        1 FSD (SELF_DRIVING), 2 Autosteer, 3 TACC. So the stream banner shows FSD as "AP: Available" and TACC as engaged
        autopilot. Display only (the drives browser and footage keeping banners)."""
        fsd = SEI._decode_sei_telemetry(reading(ap=1))
        self.assertTrue(fsd["autopilot_active"])
        self.assertIn("FSD", fsd["autopilot_state"])


# ------------------------------------------------------------------------------------------------------------ drive_footage
def utc(y, mo, d, h, mi=0):
    return int(datetime.datetime(y, mo, d, h, mi, tzinfo=datetime.timezone.utc).timestamp())


class DriveFootageTest(TempDirTest):
    def write_master(self, rows):
        os.makedirs(os.path.join(self.tmp, "drives"), exist_ok=True)
        lines = ["Started At (UTC),Ended At (UTC),Started At (Local)"] + [f"{a},{b},stale label" for a, b in rows]
        self.write(os.path.join("drives", "drives_master.csv"), "\n".join(lines) + "\n")

    def test_master_times_follow_daylight_saving(self):
        # Sydney: AEST (+10) until 2am on Sun 4 Oct 2026, then AEDT (+11)
        self.write_master([(utc(2026, 11, 1, 0), utc(2026, 11, 1, 1)), (utc(2026, 7, 1, 0), utc(2026, 7, 1, 1)),
                           ("x", "y"), (utc(2026, 10, 3, 15, 30), utc(2026, 10, 3, 16, 30))])
        m = DFT.load_master(self.tmp)
        self.assertEqual([(a, b) for a, b, _r in m], [
            (datetime.datetime(2026, 7, 1, 10, 0), datetime.datetime(2026, 7, 1, 11, 0)),
            (datetime.datetime(2026, 10, 4, 1, 30), datetime.datetime(2026, 10, 4, 3, 30)),     # across the change: 1:30 AEST to 3:30 AEDT
            (datetime.datetime(2026, 11, 1, 11, 0), datetime.datetime(2026, 11, 1, 12, 0)),
        ])
        self.assertEqual(DFT.load_master(os.path.join(self.tmp, "none")), [])

    def test_nearest_master_row_within_six_minutes(self):
        t = datetime.datetime(2026, 11, 1, 11, 0)
        master = [(t, t + datetime.timedelta(minutes=30), "a"), (t + datetime.timedelta(minutes=40), t + datetime.timedelta(minutes=50), "b")]
        self.assertEqual(DFT.find_master(master, t + datetime.timedelta(minutes=5))[2], "a")
        self.assertEqual(DFT.find_master(master, t + datetime.timedelta(minutes=37))[2], "b")
        self.assertIsNone(DFT.find_master(master, t + datetime.timedelta(minutes=20)))
        self.assertIsNone(DFT.find_master([], t))

    def test_drive_window(self):
        t0 = datetime.datetime(2026, 11, 1, 11, 0)
        pts = [dict(t=t0 + datetime.timedelta(seconds=30 * i), v=(0 if i in (0, 1, 2, 9) else 50)) for i in range(10)]
        self.assertEqual(len(DFT.drive_window(pts, None)), 8)                 # first and last moving row, one either side
        m = (t0 + datetime.timedelta(seconds=60), t0 + datetime.timedelta(seconds=120), {})
        sel = DFT.drive_window(pts, m)
        self.assertEqual((sel[0]["t"], sel[-1]["t"]), (t0 + datetime.timedelta(seconds=30), t0 + datetime.timedelta(seconds=210)))
        self.assertEqual(DFT.drive_window([dict(t=t0, v=0)], None), [dict(t=t0, v=0)])

    def test_drive_csv_power_from_pack_current(self):
        p = self.write("drive.csv", "Timestamp (AEDT),Latitude,Longitude,Speed (km/h),Power (kW),Pack Current (A),Pack Voltage (V),"
                                    "Est. Battery Range (km),Shift State\n"
                                    "2026-11-01 11:00:00,-33.5,150.0,50,12.5,,,400,D\n"
                                    "2026-11-01 11:00:30,-33.5,150.1,60,,-100,350,399,D\n"
                                    "garbage,,,,,,,,\n")
        pts = DFT.load_drive_csv(p)
        self.assertEqual(len(pts), 2)
        self.assertEqual((pts[0]["p"], pts[0]["rg"], pts[0]["sh"]), (12.5, 400.0, "D"))
        self.assertEqual(pts[1]["p"], 35.0)                                  # discharging: -(-100 A x 350 V)
        self.assertIsNone(pts[1]["el"])
        self.assertIsNone(DFT.load_drive_csv(self.write("empty.csv", "Timestamp (AEDT),Latitude\n")))


if __name__ == "__main__":
    unittest.main()
