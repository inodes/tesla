"""test_invoice_parsing.py - reading bills and charging invoices: lib/invoice_common.py's pure-Python PDF text reader
(on a tiny PDF built here), the Tesla / Exploren / Chargefox / generic extractors and their file names, the provider
detection order, and lib/charging_analyzer.py's own invoice text parser and date parser.

Every invoice text, number, name, VIN and amount below is made up. Nothing under the real invoice folders is read:
the extractors' invoice_locations.json look-up is switched off.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_invoice_parsing.py
"""
import os
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import invoice_common as IC  # noqa: E402
from lib import charging_analyzer as CA  # noqa: E402

TESLA_TEXT = """Tesla Motors Australia
TAX INVOICE 2010P0000000001
Invoice date 2026/03/14
Charging Location Example Park NSW
Energy fee 0.58 / kWh 40.25 kWh
Total GST 2.12
Total Amount (AUD) 23.35
Vehicle Identification Number: TESTVHN0000000001
"""

EXPLOREN_TEXT = """Exploren
Receipt No. 0000000042
Session started 14/03/2026 4:05 PM
Location Example Plaza Carpark Charge Point EXP-01 Connector ID 1
Total energy 30.5 kWh
Unit price net $0.60 $0.66
Total incl. Tax $20.13
Tax 10% $1.83
Transaction Ref. ABC_123
"""

CHARGEFOX_TEXT = """Chargefox
TAX INVOICE CF-12345
14/03/2026 9:10 AM
Location: Example Station Testville
22.4 kWh
Total incl. GST $14.56
"""


def no_locations_file():
    """The extractors look up a real invoice_locations.json in the user's iCloud folder; tests never read it."""
    patches = [mock.patch.object(cls, "_enrich_from_invoice_locations", lambda self, data: None)
               for cls in (IC.TeslaInvoiceExtractor, IC.ExplorenInvoiceExtractor)]
    for p in patches:
        p.start()
    return patches


class ExtractorTest(unittest.TestCase):
    def setUp(self):
        for p in no_locations_file():
            self.addCleanup(p.stop)

    def test_tesla(self):
        ext = IC.TeslaInvoiceExtractor()
        self.assertTrue(ext.detect(TESLA_TEXT, "/x/a.pdf"))
        d = ext.extract(TESLA_TEXT, "/x/a.pdf")
        self.assertEqual(d["invoice_number"], "2010P0000000001")
        self.assertEqual(d["issue_date"], "2026-03-14")
        self.assertEqual(d["location_name"], "Example Park NSW")
        self.assertEqual(d["place_safe"], "Example_Park_NSW")
        self.assertEqual((d["unit_rate"], d["energy_kwh"]), (0.58, 40.25))
        self.assertEqual((d["total_amount"], d["gst_amount"]), (23.35, 2.12))
        self.assertEqual(d["vin"], "TESTVHN0000000001")
        self.assertEqual(ext.generate_filename(d, "/x/a.pdf"), "20260314_0000_Tesla_2010P0000000001_Example_Park_NSW.pdf")

    def test_tesla_date_and_time_from_an_already_named_file(self):
        ext = IC.TeslaInvoiceExtractor()
        text = TESLA_TEXT.replace("Invoice date 2026/03/14\n", "")
        name = "/x/20260314_2205_Tesla_2010P0000000001_Example_Park_NSW.pdf"
        d = ext.extract(text, name)
        self.assertEqual((d["issue_date"], d["session_time"]), ("2026-03-14", "2205"))
        self.assertEqual(ext.generate_filename(d, name), os.path.basename(name))   # already named: unchanged

    @unittest.expectedFailure
    def test_tesla_date_with_dashes(self):
        """BUG (found by this test): the Tesla date pattern accepts 14-03-2026 (day-month-year with dashes), but
        _parse_flexible_date has no '%d-%m-%Y' format, so the date is matched and then silently dropped
        (issue_date None, and the file name falls back to the original)."""
        d = IC.TeslaInvoiceExtractor().extract(TESLA_TEXT.replace("2026/03/14", "14-03-2026"), "/x/a.pdf")
        self.assertEqual(d["issue_date"], "2026-03-14")

    def test_exploren(self):
        ext = IC.ExplorenInvoiceExtractor()
        self.assertTrue(ext.detect(EXPLOREN_TEXT, "/x/b.pdf"))
        d = ext.extract(EXPLOREN_TEXT, "/x/b.pdf")
        self.assertEqual(d["invoice_number"], "0000000042")
        self.assertEqual((d["issue_date"], d["session_time"]), ("2026-03-14", "1605"))
        self.assertEqual((d["location_name"], d["charge_point"]), ("Example Plaza Carpark", "EXP-01"))
        self.assertEqual((d["energy_kwh"], d["unit_rate"]), (30.5, 0.66))   # unit rate is the gross (incl. tax) one
        self.assertEqual((d["total_amount"], d["gst_amount"]), (20.13, 1.83))
        self.assertEqual(d["transaction_ref"], "ABC_123")
        self.assertEqual(ext.generate_filename(d, "/x/b.pdf"), "20260314_1605_Exploren_0000000042_Example_Plaza_Carpark.pdf")

    def test_exploren_seconds_in_the_time(self):
        d = IC.ExplorenInvoiceExtractor().extract(EXPLOREN_TEXT.replace("4:05 PM", "11:59:30 PM"), "/x/b.pdf")
        self.assertEqual(d["session_time"], "2359")

    def test_chargefox(self):
        ext = IC.ChargefoxInvoiceExtractor()
        d = ext.extract(CHARGEFOX_TEXT, "/x/c.pdf")
        self.assertEqual(d["invoice_number"], "CF-12345")
        self.assertEqual((d["issue_date"], d["session_time"]), ("2026-03-14", "0910"))
        self.assertEqual(d["location_name"], "Example Station Testville")
        self.assertEqual((d["energy_kwh"], d["total_amount"]), (22.4, 14.56))
        self.assertEqual(ext.generate_filename(d, "/x/c.pdf"), "20260314_0910_Chargefox_CF-12345_Example_Station_Testville.pdf")

    @unittest.expectedFailure
    def test_chargefox_location_not_taken_from_website(self):
        """BUG (found by this test): the location pattern (Station|Location|Site) is not anchored to a word, so
        'website' in a footer line above the real 'Location:' line is taken as the location."""
        text = CHARGEFOX_TEXT.replace("Chargefox\n", "Chargefox\nQuestions? Visit our website help.example\n")
        d = IC.ChargefoxInvoiceExtractor().extract(text, "/x/c.pdf")
        self.assertEqual(d["location_name"], "Example Station Testville")

    def test_generic(self):
        d = IC.GenericInvoiceExtractor().extract("Statement dated 5 March 2026\nAmount due $1,234.50\n", "/x/d.pdf")
        self.assertEqual((d["issue_date"], d["total_amount"]), ("2026-03-05", 1234.5))

    def test_flexible_date_formats(self):
        p = IC.BaseInvoiceExtractor._parse_flexible_date
        for s in ("2026/03/14", "2026-03-14", "14/03/2026", "14/03/26", "14 Mar 2026", "14 March 2026", "14 Mar 26",
                  "14/03/2026 4:05:00 PM", "14/03/2026 4:05 PM", "2026-03-14 16:05:00", "2026-03-14 16:05"):
            with self.subTest(s=s):
                self.assertEqual(p(s), "2026-03-14")
        self.assertIsNone(p("not a date"))

    def test_clean_place_name(self):
        c = IC.BaseInvoiceExtractor._clean_place_name
        self.assertEqual(c(None), "Charging")
        self.assertEqual(c(""), "Charging")
        self.assertEqual(c("Example Plaza, Testville - Level 2"), "Example_Plaza_Testville_Level_2")
        self.assertEqual(c(" / , "), "Charging")


class DetectionOrderTest(unittest.TestCase):
    def setUp(self):
        for p in no_locations_file():
            self.addCleanup(p.stop)

    def run_with(self, text, path="/x/in.pdf", forced=None):
        with mock.patch.object(IC.PDFTextExtractor, "extract_text", return_value=text):
            ext, data, raw = IC.detect_and_extract_invoice(path, forced_provider=forced)
        self.assertEqual(raw, text)
        return ext.name, data

    def test_each_provider_is_picked(self):
        self.assertEqual(self.run_with(TESLA_TEXT)[0], "tesla")
        self.assertEqual(self.run_with(EXPLOREN_TEXT)[0], "exploren")
        self.assertEqual(self.run_with(CHARGEFOX_TEXT)[0], "chargefox")
        self.assertEqual(self.run_with("Your gas bill\nTax invoice\nTotal $10.00")[0], "agl")
        self.assertEqual(self.run_with("A receipt 5 March 2026 $3.00")[0], "generic")

    def test_forced_provider(self):
        self.assertEqual(self.run_with(TESLA_TEXT, forced="exploren")[0], "exploren")
        self.assertEqual(self.run_with(TESLA_TEXT, forced="auto")[0], "tesla")
        self.assertEqual(self.run_with(TESLA_TEXT, forced="nobody")[0], "generic")

    def test_charging_router(self):
        r = IC.TeslaChargingInvoiceExtractor()
        self.assertEqual(r.extract(EXPLOREN_TEXT, "/x/b.pdf")["network"], "Exploren")
        self.assertEqual(r.extract(CHARGEFOX_TEXT, "/x/c.pdf")["network"], "Chargefox")
        self.assertEqual(r.extract(TESLA_TEXT, "/x/a.pdf")["network"], "Tesla")


def make_pdf(content_stream, cmap_stream=None, compress=True):
    """A minimal PDF: one page, font /F1 (with a ToUnicode map when cmap_stream is given), one content stream."""
    def stream_obj(n, data, flate):
        if flate:
            data = zlib.compress(data)
            # the reader trims newlines around the stream; keep the made-up data clear of them
            assert data[:1] not in (b"\r", b"\n") and data[-1:] not in (b"\r", b"\n")
        f = b" /Filter /FlateDecode" if flate else b""
        return b"%d 0 obj\n<< /Length %d%s >>\nstream\n%s\nendstream\nendobj\n" % (n, len(data), f, data)

    out = b"%PDF-1.4\n"
    out += b"1 0 obj\n<< /Type /Page /Resources << /Font << /F1 2 0 R >> >> /Contents 3 0 R >>\nendobj\n"
    if cmap_stream:
        out += b"2 0 obj\n<< /Type /Font /Subtype /Type0 /Encoding /Identity-H /ToUnicode 4 0 R >>\nendobj\n"
    else:
        out += b"2 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    out += stream_obj(3, content_stream, compress)
    if cmap_stream:
        out += stream_obj(4, cmap_stream, False)
    return out + b"%%EOF\n"


CMAP = b"""/CIDInit /ProcSet findresource begin
begincmap
2 beginbfchar
<0001> <0041>
<0002> <0042>
endbfchar
1 beginbfrange
<0010> <0012> <0030>
endbfrange
endcmap
"""


class PdfTextTest(unittest.TestCase):
    def read(self, pdf_bytes):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "made_up.pdf")
            with open(path, "wb") as f:
                f.write(pdf_bytes)
            return IC.PDFTextExtractor._extract_pure_python(path)

    def test_plain_literal_text_compressed_and_not(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                txt = self.read(make_pdf(b"BT /F1 12 Tf (Total Amount \\(AUD\\) 12.50) Tj ET", compress=compress))
                self.assertEqual(txt, "Total Amount (AUD) 12.50")

    def test_identity_h_hex_through_tounicode(self):
        # <0001>=A, <0002>=B, <0010>-<0012> = 0,1,2
        txt = self.read(make_pdf(b"BT /F1 12 Tf <0001000200100012> Tj ET", CMAP))
        self.assertEqual(txt, "AB02")

    def test_tj_array_of_hex(self):
        txt = self.read(make_pdf(b"BT /F1 12 Tf [<0001> -120 <0002>] TJ ET", CMAP))
        self.assertEqual(txt, "AB")

    @unittest.expectedFailure
    def test_tj_array_of_two_byte_literals(self):
        """BUG (found by this test): a 2-byte (Identity-H) literal string inside a TJ array is decoded with
        int(bytes, "big") instead of int.from_bytes(bytes, "big"), which raises TypeError and stops the whole
        pure-Python reader (used when pypdf and pdfplumber are not installed)."""
        txt = self.read(make_pdf(b"BT /F1 12 Tf [(\x00\x01\x00\x02)] TJ ET", CMAP))
        self.assertEqual(txt, "AB")

    def test_missing_file(self):
        self.assertEqual(IC.PDFTextExtractor._extract_pure_python("/nonexistent/made_up.pdf"), "")


class AnalyzerInvoiceTextTest(unittest.TestCase):
    """lib/charging_analyzer.py's TeslaInvoiceParser.parse_invoice_text (label on one line, value on the next)."""

    LINES = "\n".join([
        "Tesla Motors Australia", "Invoice Number", "2010P0000000002", "Invoice date", "2026/03/14 10:15",
        "Charging Location", "Example Park NSW", "Energy fee 0.58 / kWh 40.25", "Total Amount (AUD)", "23.35",
        "Total GST", "2.12", "Vehicle Identification Number", "TESTVHN0000000001",
    ])

    def test_label_then_value(self):
        r = CA.TeslaInvoiceParser.parse_invoice_text(self.LINES, source_file="/x/made_up.pdf")
        self.assertEqual(r["invoice_number"], "2010P0000000002")
        self.assertEqual(r["date"], datetime(2026, 3, 14, 10, 15))
        self.assertEqual(r["location_raw"], "Example Park NSW")
        self.assertEqual((r["energy_kwh"], r["unit_rate"]), (40.25, 0.58))
        self.assertEqual((r["total_cost"], r["gst"]), (23.35, 2.12))
        self.assertEqual(r["gross_rate"], round(23.35 / 40.25, 2))
        self.assertEqual(r["vin"], "TESTVHN0000000001")
        self.assertEqual(r["network"], "Tesla Supercharger")

    def test_network_from_text(self):
        text = self.LINES.replace("Tesla Motors Australia", "Exploren receipt")
        self.assertEqual(CA.TeslaInvoiceParser.parse_invoice_text(text)["network"], "Exploren")

    def test_unit_rate_worked_out_when_not_printed(self):
        text = "Invoice date\n2026/03/14\nEnergy delivered 20.00 kWh\nTotal Amount\n10.00\n"
        r = CA.TeslaInvoiceParser.parse_invoice_text(text)
        self.assertEqual((r["energy_kwh"], r["total_cost"], r["unit_rate"]), (20.0, 10.0, 0.5))

    def test_nothing_useful_is_none(self):
        self.assertIsNone(CA.TeslaInvoiceParser.parse_invoice_text(""))
        self.assertIsNone(CA.TeslaInvoiceParser.parse_invoice_text("hello\nworld"))

    @unittest.expectedFailure
    def test_total_on_the_same_line_as_its_label(self):
        """BUG (found by this test): when the total's label and value are on one line ('Total Amount (AUD) 23.35',
        as lib/invoice_common's Tesla extractor expects), the line loop takes the NEXT line's number instead -
        here the GST, 2.12 - so the charge is priced at its GST."""
        text = self.LINES.replace("Total Amount (AUD)\n23.35\nTotal GST\n2.12", "Total Amount (AUD) 23.35\nTotal GST 2.12")
        self.assertIn("Total Amount (AUD) 23.35\nTotal GST 2.12", text)
        self.assertEqual(CA.TeslaInvoiceParser.parse_invoice_text(text)["total_cost"], 23.35)


class AnalyzerDateParseTest(unittest.TestCase):
    def test_formats(self):
        want = datetime(2026, 3, 14, 16, 5)
        for s in ("2026-03-14 16:05", "2026-03-14 4:05 PM", "2026/03/14 16:05", "14/03/2026 16:05",
                  "14-03-2026 16:05", "14 Mar 2026 16:05", "14 March 2026 4:05 PM", "2026-03-14 16:05 AEDT"):
            with self.subTest(s=s):
                self.assertEqual(CA.parse_flexible_date(s), want)
        self.assertEqual(CA.parse_flexible_date("20260314"), datetime(2026, 3, 14))
        self.assertIsNone(CA.parse_flexible_date(""))
        self.assertIsNone(CA.parse_flexible_date(None))
        self.assertIsNone(CA.parse_flexible_date("not a date"))

    def test_iso_utc_stays_utc_aware(self):
        self.assertEqual(CA.parse_flexible_date("2026-09-05T00:47:11Z"), datetime(2026, 9, 5, 0, 47, 11, tzinfo=timezone.utc))

    def test_relative_words(self):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(CA.parse_flexible_date("today"), today)
        self.assertEqual(CA.parse_flexible_date("Yesterday"), today - timedelta(days=1))
        # a weekday name is the most recent past one, never today
        name = today.strftime("%A")
        self.assertEqual(CA.parse_flexible_date(name), today - timedelta(days=7))

    def test_aware_to_local_naive(self):
        aware = datetime(2026, 1, 15, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(CA._aware_to_local_naive(aware), datetime(2026, 1, 15, 12, 0))          # AEDT
        self.assertEqual(CA._aware_to_local_naive(aware, "Australia/Perth"), datetime(2026, 1, 15, 9, 0))
        naive = datetime(2026, 1, 15, 1, 0)
        self.assertIs(CA._aware_to_local_naive(naive), naive)

    def test_rate_str(self):
        self.assertEqual(CA._rate_str(0.5), "$0.50")
        self.assertEqual(CA._rate_str(None), "$?")


if __name__ == "__main__":
    unittest.main()
