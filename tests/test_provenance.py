"""Header reader for dashcam clips: UTC creation time, place text, car-clock offset, and a one-hour back-jump."""
import calendar, os, struct, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import teslacam_provenance as P

MAC = 2082844800


def box(kind, payload):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def hdr(kind, created, modified):
    return box(kind, b"\0\0\0\0" + struct.pack(">II", created + MAC, modified + MAC) + b"\0" * 20)


def tag(kind, text):
    d = text.encode()
    return box(kind, box(b"data", b"\0\0\0\1\0\0\0\0" + d))


def clip(path, created, street="Chatham Rd", suburb="West Ryde", tracks_extra=0):
    ilst = box(b"ilst", tag(b"\xa9cmt", street) + tag(b"\xa9nam", suburb))
    meta = box(b"meta", b"\0\0\0\0" + box(b"hdlr", b"\0" * 25) + ilst)
    trak = box(b"trak", hdr(b"tkhd", created + tracks_extra, created + 60) + box(b"mdia", hdr(b"mdhd", created, created + 60)) + box(b"udta", meta))
    moov = box(b"moov", hdr(b"mvhd", created, created + 60) + trak)
    open(path, "wb").write(box(b"ftyp", b"mp42" + b"\0" * 8) + box(b"mdat", b"x" * 100) + moov)  # moov AFTER mdat, as the car writes it


def utc(*a):
    return calendar.timegm(a)


class Header(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_reads_utc_and_place(self):
        p = os.path.join(self.d, "2026-10-20_09-32-39-front.mp4")
        c = utc(2026, 10, 19, 22, 32, 39)
        clip(p, c)
        h = P.read_header(p)
        self.assertEqual((h["created"], h["modified"], h["street"], h["suburb"], h["tracks_agree"], h["error"]), (c, c + 60, "Chatham Rd", "West Ryde", True, ""))
        self.assertEqual(P.offset(p, h), 11 * 3600)  # 20 Oct is NSW daylight time

    def test_back_jump_is_visible_and_names_differ_from_utc(self):
        # NSW(+11) -> QLD(+10): the car's clock goes back an hour, so a NAME can repeat while the UTC creation times stay distinct
        a, b = os.path.join(self.d, "a", "2026-10-04_12-00-00-front.mp4"), os.path.join(self.d, "b", "2026-10-04_12-00-00-front.mp4")
        os.makedirs(os.path.dirname(a)); os.makedirs(os.path.dirname(b))
        clip(a, utc(2026, 10, 4, 1, 0, 0))   # +11
        clip(b, utc(2026, 10, 4, 2, 0, 0))   # +10
        ha, hb = P.read_header(a), P.read_header(b)
        self.assertEqual(os.path.basename(a), os.path.basename(b))
        self.assertNotEqual(ha["created"], hb["created"])
        self.assertEqual((P.offset(a, ha), P.offset(b, hb)), (11 * 3600, 10 * 3600))

    def test_tracks_disagree_flagged(self):
        p = os.path.join(self.d, "2026-10-20_09-32-39-front.mp4")
        clip(p, utc(2026, 10, 19, 22, 32, 39), tracks_extra=5)
        self.assertFalse(P.read_header(p)["tracks_agree"])

    def test_junk_and_missing(self):
        p = os.path.join(self.d, "2026-10-20_09-32-39-front.mp4")
        open(p, "wb").write(b"not an mp4 at all")
        self.assertIsNone(P.read_header(p)["created"])
        self.assertTrue(P.read_header(p)["error"])
        self.assertIn("Error", P.read_header(os.path.join(self.d, "nope.mp4"))["error"])

    def test_name_stamp(self):
        self.assertEqual(P.name_stamp("x/2026-10-20_09-32-39-front.mp4"), utc(2026, 10, 20, 9, 32, 39))
        self.assertIsNone(P.name_stamp("x/thumb.png"))


if __name__ == "__main__":
    unittest.main()
