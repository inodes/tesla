"""
invoice_common.py - Generic PDF Invoice Ingestion & Extraction Library
========================================================================
Provides a pure-standard-library, zero-external-dependency PDF text & layout
extractor, alongside a pluggable provider framework for detecting, inspecting,
extracting metadata from, and standardizing names of PDF invoices (AGL, Tesla
Supercharging, Exploren, Chargefox, and Generic invoices).
"""

import datetime
import os
import re
import zlib
from typing import Any, Dict, List, Optional, Tuple


# Precompiled regex patterns for maximum parsing performance
RE_STREAM = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.DOTALL)
RE_BT_ET = re.compile(rb"BT(.*?)ET", re.DOTALL)
RE_TJ_LIT = re.compile(rb"\((.*?)\)\s*Tj", re.DOTALL)
RE_TJ_HEX = re.compile(rb"<([0-9a-fA-F\s]+)>\s*Tj", re.DOTALL)
RE_TJ_ARR = re.compile(rb"\[(.*?)\]\s*TJ", re.DOTALL)
RE_ARR_ITEMS = re.compile(rb"<([0-9a-fA-F\s]+)>|\(((?:\\.|[^\\)])*)\)", re.DOTALL)
RE_FONTS = re.compile(rb"/([A-Za-z0-9_]+)\s+(\d+)\s+0\s+R")
RE_TOUNICODE = re.compile(rb"/ToUnicode\s+(\d+)\s+0\s+R")
RE_BFCHAR = re.compile(r"beginbfchar(.*?)endbfchar", re.DOTALL)
RE_BFRANGE = re.compile(r"beginbfrange(.*?)endbfrange", re.DOTALL)
RE_PAIR = re.compile(r"<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>")
RE_RANGE_SEQ = re.compile(r"<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>")
RE_RANGE_ARR = re.compile(r"<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>\s*\[(.*?)\]", re.DOTALL)


# ==============================================================================
# 1. PURE PYTHON ZERO-DEPENDENCY PDF TEXT EXTRACTOR
# ==============================================================================

class PDFTextExtractor:
    """
    High-performance, pure-Python PDF text and metadata extractor.
    Handles uncompressed streams, FlateDecode (zlib and raw deflate),
    Font CMaps (/ToUnicode), 2-byte Identity-H CIDs, and token reconstruction.
    """

    @classmethod
    def extract_text(cls, pdf_path: str) -> str:
        """Extract full plain text from PDF with layout reconstruction."""
 # Fast path: if pypdf or pdfplumber is available, try it first
        try:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            extracted = [page.extract_text() or "" for page in reader.pages]
            full_txt = "\n".join(extracted).strip()
            if full_txt:
                return full_txt
        except Exception:
            pass

        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                extracted = [page.extract_text() or "" for page in pdf.pages]
            full_txt = "\n".join(extracted).strip()
            if full_txt:
                return full_txt
        except Exception:
            pass

        return cls._extract_pure_python(pdf_path)

    @classmethod
    def _extract_pure_python(cls, pdf_path: str) -> str:
        """Pure-Python stream & font CMap parser."""
        if not os.path.isfile(pdf_path):
            return ""

        try:
            with open(pdf_path, "rb") as f:
                content = f.read()
        except Exception:
            return ""

        objects: Dict[int, bytes] = {}
        for m in re.finditer(rb"(\d+)\s+0\s+obj\s*(.*?)\s*endobj", content, re.DOTALL):
            objects[int(m.group(1))] = m.group(2)

        def get_stream(body: bytes) -> bytes:
            m = RE_STREAM.search(body)
            if not m:
                return b""
            raw = m.group(1)
            try:
                return zlib.decompress(raw)
            except Exception:
                try:
                    return zlib.decompress(raw, -15)
                except Exception:
                    return raw

 # 1. Parse font CMaps (/ToUnicode)
        font_cmaps: Dict[int, Dict[int, str]] = {}
        for oid, body in objects.items():
            if b"/ToUnicode" in body:
                tu_m = RE_TOUNICODE.search(body)
                if tu_m:
                    tu_id = int(tu_m.group(1))
                    tu_body = objects.get(tu_id, b"")
                    tu_stream = get_stream(tu_body).decode("latin1", errors="replace")
                    cmap: Dict[int, str] = {}

 # Parse bfchar
                    for block in RE_BFCHAR.findall(tu_stream):
                        for src, dst in RE_PAIR.findall(block):
                            s_val = int(src, 16)
                            dst_str = "".join(chr(int(dst[i:i+4], 16)) for i in range(0, len(dst), 4))
                            cmap[s_val] = dst_str

 # Parse bfrange (both sequential range and array forms)
                    for block in RE_BFRANGE.findall(tu_stream):
 # Form 1: <start> <end> <dest_start>
                        for s_hex, e_hex, d_hex in RE_RANGE_SEQ.findall(block):
                            s_val = int(s_hex, 16)
                            e_val = int(e_hex, 16)
                            d_val = int(d_hex, 16)
                            for i in range(e_val - s_val + 1):
                                cmap[s_val + i] = chr(d_val + i)

 # Form 2: <start> <end> [ <dest1> <dest2> ... ]
                        for s_hex, e_hex, arr_str in RE_RANGE_ARR.findall(block):
                            s_val = int(s_hex, 16)
                            dests = re.findall(r"<([0-9a-fA-F]+)>", arr_str)
                            for i, d_h in enumerate(dests):
                                d_str = "".join(chr(int(d_h[j:j+4], 16)) for j in range(0, len(d_h), 4))
                                cmap[s_val + i] = d_str

                    font_cmaps[oid] = cmap

 # 2. Map resource fonts (/F1, /F2, etc.) to font object IDs
        res_fonts: Dict[str, int] = {}
        for oid, body in objects.items():
            if b"/Font" in body:
                for fname, fid in RE_FONTS.findall(body):
                    res_fonts[fname.decode("latin1")] = int(fid)

 # 3. Extract text from content streams
        chunks: List[str] = []
        for oid, body in objects.items():
            if b"/Contents" in body or b"/Length" in body:
                st = get_stream(body)
                if not st or b"BT" not in st:
                    continue

                for bt in RE_BT_ET.findall(st):
                    f_m = re.search(rb"/([A-Za-z0-9_]+)\s+\d+(?:\.\d+)?\s+Tf", bt)
                    curr_font = f_m.group(1).decode("latin1") if f_m else None
                    fid = res_fonts.get(curr_font or "", -1)
                    cmap = font_cmaps.get(fid, {})

 # Literal strings
                    for t in RE_TJ_LIT.findall(bt):
                        lit = t.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
                        if len(lit) >= 2 and len(lit) % 2 == 0 and any(cmap.get(int.from_bytes(lit[i:i+2], "big")) for i in range(0, min(10, len(lit)), 2)):
                            for i in range(0, len(lit), 2):
                                cid = int.from_bytes(lit[i:i+2], "big")
                                chunks.append(cmap.get(cid, chr(cid) if 32 <= cid < 128 else ""))
                        else:
                            for b in lit:
                                chunks.append(cmap.get(b, chr(b) if 32 <= b < 128 else ""))

 # Hex strings
                    for hx in RE_TJ_HEX.findall(bt):
                        hx_clean = re.sub(rb"\s+", b"", hx).decode("ascii", errors="ignore")
                        for i in range(0, len(hx_clean), 4):
                            cid = int(hx_clean[i:i+4], 16)
                            chunks.append(cmap.get(cid, chr(cid) if 32 <= cid < 128 else ""))

 # TJ Arrays
                    for arr in RE_TJ_ARR.findall(bt):
                        for hx_item, lit_item in RE_ARR_ITEMS.findall(arr):
                            if hx_item:
                                hx_c = re.sub(rb"\s+", b"", hx_item).decode("ascii", errors="ignore")
                                for i in range(0, len(hx_c), 4):
                                    cid = int(hx_c[i:i+4], 16)
                                    chunks.append(cmap.get(cid, chr(cid) if 32 <= cid < 128 else ""))
                            elif lit_item:
                                lit_c = lit_item.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
                                if len(lit_c) >= 2 and len(lit_c) % 2 == 0 and any(cmap.get(int.from_bytes(lit_c[i:i+2], "big")) for i in range(0, min(10, len(lit_c)), 2)):
                                    for i in range(0, len(lit_c), 2):
                                        cid = int(lit_c[i:i+2], "big")
                                        chunks.append(cmap.get(cid, chr(cid) if 32 <= cid < 128 else ""))
                                else:
                                    for b in lit_c:
                                        chunks.append(cmap.get(b, chr(b) if 32 <= b < 128 else ""))
                    chunks.append(" ")

        text = "".join(chunks)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()


# ==============================================================================
# 2. BASE & PLUGGABLE INVOICE EXTRACTORS
# ==============================================================================

class BaseInvoiceExtractor:
    """Base class defining invoice extractor interface."""
    name: str = "base"
    display_name: str = "Base Invoice"

    def detect(self, text: str, pdf_path: str) -> bool:
        """Return True if this PDF matches this extractor."""
        raise NotImplementedError

    def extract(self, text: str, pdf_path: str) -> Dict[str, Any]:
        """Extract structured metadata from invoice text."""
        raise NotImplementedError

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        """Generate standardized filename for this invoice."""
        raise NotImplementedError

    def resolve_target_dir(self, data: Dict[str, Any], original_path: str) -> Optional[str]:
        """Resolve destination directory if moving/importing."""
        return None

    @staticmethod
    def _parse_flexible_date(s: str) -> Optional[str]:
        s = s.strip()
        for fmt in (
            "%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y",
            "%d %b %Y", "%d %B %Y", "%d %b %y",
            "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p",
            "%Y/%m/%d %I:%M:%S %p", "%Y/%m/%d %I:%M %p",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        ):
            try:
                dt = datetime.datetime.strptime(s, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    @staticmethod
    def _clean_place_name(loc_raw: Optional[str]) -> str:
        """Standardize raw location text into file-safe place string."""
        if not loc_raw:
            return "Charging"
        loc = loc_raw.strip()
 # Known custom site normalizations
        if "Bunnings Rydalmere" in loc:
            return "Bunnings_Rydalmere"
        if "Bunnings Gladesville" in loc:
            return "Bunnings_Gladesville"
        if "Miranda" in loc:
            return "Miranda_NSW_Parraweena_Rd"
        if "Gosford" in loc or "West Gosford" in loc:
            return "West_Gosford_NSW"
        if "Macquarie" in loc:
            return "Macquarie_NSW"

 # General clean
        clean = re.sub(r"(?:Talavera Road.*|Manns Rd.*|Parraweena Rd.*|\d{4}\s+North Ryde.*)", "", loc)
        clean = re.sub(r"[\/,–\-]+", " ", clean)
        clean = re.sub(r"\s+", "_", clean).strip("_")
        return clean or "Charging"


# ------------------------------------------------------------------------------
# 2.1 AGL POWER INVOICE EXTRACTOR (TODO-012 / REQ-092)
# ------------------------------------------------------------------------------

class AGLInvoiceExtractor(BaseInvoiceExtractor):
    """
    AGL Electricity & Gas Statement / Tax Invoice Extractor.
    Extracts Account Number, Issue Date, Due Date, Service Type, Total Amount,
    and formats filename as: Statement_{service_type}_{YYYYMMDD}.pdf
    """
    name = "agl"
    display_name = "AGL Energy"

    def detect(self, text: str, pdf_path: str) -> bool:
        t_low = text.lower()
        p_low = os.path.basename(pdf_path).lower()
        if "agl.com.au" in t_low or "agl sales" in t_low or "agl electricity" in t_low:
            return True
        if "here's your monthly electricity bill" in t_low or "national metering identifier (nmi)" in t_low:
            return True
        if ("electricity bill" in t_low or "gas bill" in t_low) and ("tax invoice" in t_low or "statement" in t_low):
            return True
        if "statement_electricity_" in p_low or "statement_gas_" in p_low:
            return True
        return False

    def extract(self, text: str, pdf_path: str) -> Dict[str, Any]:
        fname = os.path.basename(pdf_path)
        data: Dict[str, Any] = {
            "provider": self.name,
            "provider_display": self.display_name,
            "source_file": fname,
            "source_path": pdf_path,
            "account_number": None,
            "invoice_number": None,
            "nmi": None,
            "issue_date": None,
            "due_date": None,
            "period_start": None,
            "period_end": None,
            "service_type": "electricity",
            "total_amount": None,
            "gst_amount": None,
            "usage_kwh": None,
            "solar_export_kwh": None,
            "peak_demand_kw": None,
        }

 # 1. Service Type
        t_low = text.lower()
        if "gas bill" in t_low or "statement_gas_" in fname.lower() or "natural gas" in t_low:
            data["service_type"] = "gas"
        else:
            data["service_type"] = "electricity"

 # 2. Account Number
 # BUG-126 (2026-09-21): older AGL statements (2020-2022) render this as two separate
 # blocks - a run of labels ("Name: Account number: Supply address:") followed by a run
 # of values in the same order ("<Name> <Surname> <account number> <street address>...") -
 # rather than a label immediately followed by its own value. The old strict-adjacency
 # regex never matched that layout at all. Widened to allow up to 120 characters of
 # intervening text (other labels, newlines) between the label and the actual account
 # number, which AGL formats consistently as three digit groups (4-3-3, e.g.
 # "1234 567 890") - that specific shape keeps this from false-matching an unrelated
 # number (like the NMI) that happens to fall within the window.
        acc_m = re.search(r"Account\s*number\s*:?[\s\S]{0,120}?(\d{4}\s?\d{3}\s?\d{3})\b", text, re.IGNORECASE)
        if acc_m:
            data["account_number"] = re.sub(r"\s+", "", acc_m.group(1))

 # 3. NMI (National Metering Identifier)
        nmi_m = re.search(r"National\s*Metering\s*Identifier\s*\(NMI\)[\s:]*([0-9]{10,11})", text, re.IGNORECASE)
        if nmi_m:
            data["nmi"] = nmi_m.group(1)

 # 4. Issue Date / Bill Date
        dt_m = re.search(r"(?:Issue\s*date|Date\s*of\s*issue|Bill\s*date|Statement\s*date)[\s:]*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
        if dt_m:
            data["issue_date"] = self._parse_flexible_date(dt_m.group(1))

 # Fallback date from existing filename: e.g. Statement_electricity_20260728.pdf
        if not data["issue_date"]:
            fn_dt_m = re.search(r"(\d{4})(\d{2})(\d{2})", fname)
            if fn_dt_m:
                try:
                    data["issue_date"] = f"{fn_dt_m.group(1)}-{fn_dt_m.group(2)}-{fn_dt_m.group(3)}"
                except Exception:
                    pass

 # 5. Due Date / Direct Debit Date
        due_m = re.search(r"(?:Direct\s*Debit\s*date|Due\s*date|Payment\s*due)[\s:]*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})", text, re.IGNORECASE)
        if due_m:
            data["due_date"] = self._parse_flexible_date(due_m.group(1))

 # 6b. Bill period (REQ-112, extended REQ-123) - e.g. "Bill period: 25 Jul
 # 2026 to 24 Aug 2026 (31 days)". This is the real billing window the rate
 # figures below apply to - more useful than issue_date for pinning down
 # when a rate change actually took effect. Older (pre-2015) AGL bills use
 # "Supply period 18 Oct 2013 to 20 Jan 2014" instead of "Bill period:" -
 # same date format, different label - so both are matched here.
        period_m = re.search(
            r"(?:Bill|Supply)\s*period\s*:?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\s+to\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
            text, re.IGNORECASE,
        )
        if period_m:
            data["period_start"] = self._parse_flexible_date(period_m.group(1))
            data["period_end"] = self._parse_flexible_date(period_m.group(2))

 # 6. Total Amount
        amt_m = re.search(r"(?:Direct\s*Debit\s*amount|Total\s*amount\s*due|Total\s*charges|Amount\s*due)[\s:]*\$([0-9,]+\.\d{2})", text, re.IGNORECASE)
        if amt_m:
            try:
                data["total_amount"] = float(amt_m.group(1).replace(",", ""))
            except ValueError:
                pass
        else:
 # BUG-126: a "Payment not required. Your credit will be carried forward" month has no
 # "Direct Debit amount"/"Total amount due" line at all - the only real dollar figure on
 # the bill is "Account balance $X.XXcr". Capture it as a negative amount (credit, nothing
 # owed) rather than leaving total_amount null - null previously made these bills
 # invisible to any downstream cost reconciliation instead of correctly showing $0 owed.
            bal_m = re.search(r"Account\s*balance\s*\$([0-9,]+\.\d{2})(cr)?", text, re.IGNORECASE)
            if bal_m:
                try:
                    val = float(bal_m.group(1).replace(",", ""))
                    data["total_amount"] = -val if bal_m.group(2) else val
                except ValueError:
                    pass

 # 7. GST Amount
        gst_m = re.search(r"(?:Includes\s*GST\s*of|Total\s*GST|GST\s*includes)[\s:]*\$([0-9,]+\.\d{2})", text, re.IGNORECASE)
        if gst_m:
            try:
                data["gst_amount"] = float(gst_m.group(1).replace(",", ""))
            except ValueError:
                pass

 # 8. Invoice / Statement Number
        inv_m = re.search(r"(?:Tax\s*Invoice|Statement\s*No|Invoice\s*number)[\s:]*([0-9]{8,12})", text, re.IGNORECASE)
        if inv_m:
            data["invoice_number"] = inv_m.group(1)
        elif fname.replace(".pdf", "").replace("-1", "").isdigit() and len(fname.replace(".pdf", "").replace("-1", "")) >= 8:
            data["invoice_number"] = fname.replace(".pdf", "").replace("-1", "")

        return data

    _RATE_ROW_RE = re.compile(r"([\d,]+\.?\d*)\s*(kWh|kW|days)\s+\$([\d.]+)\s+\$[\d,.]+([^\n]*)")

 # REQ-126: AGL changed this table's own column ORDER at some point
 # between mid-2022 and mid-2024 (confirmed real, three layouts seen:
 # pre-~2022 flat-rate bills and ~2022-2024 TOU bills both print
 # "$<amount><units><type> $<price><label>" - amount-first, no space
 # before the unit or the label; ~2024-on bills print
 # "<units> <type> $<price> $<amount><label>" - units-first, which is
 # what _RATE_ROW_RE above already matches). This is the amount-first
 # shape's own row pattern - same 4 groups (units, type, price, label)
 # so callers can treat rows from both regexes identically.
    _OLD_ROW_RE = re.compile(r"\$[\d,]+\.\d{2}(?:cr)?([\d,]+\.?\d*)\s?(kWh|kW|days)\s*\$([\d.]+)([^\n]*)")

    def _find_rate_rows(self, block_text: str):
        """Combined (units, type, price, label) rows from BOTH real
        column orderings AGL has used (see _OLD_ROW_RE above) - every
        caller that used to call _RATE_ROW_RE.findall() directly should
        call this instead so both eras of bill are covered."""
        return self._RATE_ROW_RE.findall(block_text) + self._OLD_ROW_RE.findall(block_text)

    def _find_rate_rows_with_pos(self, block_text: str):
        """Like _find_rate_rows(), but keeps each match's (start, end)
        span AND its own same-line label capture (group 4), sorted by
        position - (start, end, units, type, price, same_line_label)
        tuples. REQ-152: a row's own tier LABEL (Peak/Off-peak/Shoulder)
        is not reliably on the same line as its amount - a real bill
        wraps a Peak or Shoulder row's own multi-line time-window
        description (e.g. "2pm-8pm (Mon-Fri excl. public\nholidays)")
        onto several lines, pushing the actual "Peak"/"Shoulder" label
        text onto its OWN following line, so this row's regex-captured
        group 4 (same_line_label - everything up to the first newline
        after the amount) only gets a PARTIAL, keyword-less fragment of
        the window text. Only Off-peak's short "All other times" window,
        and the oldest flat bills' plain "General Usage"/"Controlled
        Load 2" labels, happen to fit on one line - confirmed real on a
        2023-05-25 bill during REQ-152's own regression check (see
        AGENTS.local.md). _extract_block_rates() combines this
        same_line_label with the text gap up to the NEXT row's start to
        cover both cases: same-line labels are already complete in
        group 4 (the gap after them is empty/irrelevant); wrapped labels
        are completed by that trailing gap text."""
        matches = list(self._RATE_ROW_RE.finditer(block_text)) + list(self._OLD_ROW_RE.finditer(block_text))
        rows = [(m.start(), m.end(), m.group(1), m.group(2), m.group(3), m.group(4)) for m in matches]
        rows.sort(key=lambda r: r[0])
        return rows

    _PLAN_CHANGE_RE = re.compile(
        r"Energy\s*plan\s*&\s*price\s*change\s*-\s*"
        r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s+to\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s*\((\d+)\s*days?\)",
        re.IGNORECASE,
    )

    def _extract_block_rates(self, block_text: str) -> Dict[str, Any]:
        """One usage/supply sub-table's worth of rates - see extract_rates()
        for the row-order assumption this relies on. Returns None values for
        anything not found in THIS block (e.g. a demand-only block, see
        extract_rates(), has no kWh/days rows at all).

        REQ-152: classifies each kWh row by its own LABEL text (Peak/
        Off-peak/Shoulder/General Usage/Controlled load) rather than
        assuming a fixed row count/order - the old ">=3 rows" assumption
        silently found nothing at all on a genuine 2-tier Peak/Off-peak-
        only bill (no Shoulder line at all - the real shape of AGL's own
        2026-09-07 plan change), which would have meant a future bill
        under that new structure never got its rates captured by
        sync_agl_rates.py.

        The label text is NOT reliably on the same line as its row's own
        amount - a real bill wraps a long multi-line time-window
        description (e.g. Peak's "2pm-8pm (Mon-Fri excl. public\n
        holidays)") between the amount and the tier label, pushing the
        label onto its own following line; only Off-peak's short "All
        other times" window happens to fit on one line, which is why a
        same-line capture only ever seemed to work for that one tier
        (confirmed on a real 2023-05-25 bill during REQ-152's own
        regression check - see AGENTS.local.md). So this uses
        _find_rate_rows_with_pos() and takes each row's label from the
        text SPAN between that row's own end and the next row's start
        (whatever tier keyword appears anywhere in that span), not a
        same-line regex group.

        A kWh row whose span names none of Peak/Shoulder/Off-peak is
        "other" (Controlled Load rows land here and are correctly
        ignored) UNLESS it says "general usage" (the oldest pre-TOU
        flat bills, REQ-126). A period with only ONE of Peak/Shoulder/
        Off-peak actually printed (no second tier to differentiate
        against) is a flat rate for that period, whatever single tier
        label AGL's own bill happened to print on it - confirmed real on
        this account's own 2014-2015 bills, which print a lone "Peak"
        label despite genuine TOU differentiation not starting until
        2022-04-06 (AGENTS.local.md REQ-151)."""
        rows = self._find_rate_rows_with_pos(block_text)
        out: Dict[str, Any] = {
            "usage_rate_per_kwh": None,
            "rate_tou_mismatch": False,
            "supply_charge_per_day": None,
            "peak_rate_per_kwh": None,
            "shoulder_rate_per_kwh": None,
            "offpeak_rate_per_kwh": None,
        }

        by_tier = {"peak": [], "shoulder": [], "offpeak": [], "other": []}
        for idx, (start, end, units, rtype, price, same_line_label) in enumerate(rows):
            if rtype != "kWh":
                continue
            next_start = rows[idx + 1][0] if idx + 1 < len(rows) else len(block_text)
            # Same-line label (group 4, e.g. plain "General Usage") plus
            # whatever text sits between this row's end and the next row's
            # start (where a WRAPPED multi-line Peak/Shoulder window
            # description pushes the real tier keyword) - see
            # _find_rate_rows_with_pos()'s docstring.
            label = (same_line_label + block_text[end:next_start]).lower()
            if "shoulder" in label:
                by_tier["shoulder"].append(price)
            elif "off peak" in label or "off-peak" in label or "offpeak" in label:
                by_tier["offpeak"].append((price, label))
            elif "peak" in label:
                by_tier["peak"].append((price, label))
            else:
                by_tier["other"].append((price, label))

        tiers_found = [k for k in ("peak", "shoulder", "offpeak") if by_tier[k]]
        if len(tiers_found) >= 2:
            def _price(tier):
                v = by_tier[tier][0]
                return float(v[0] if isinstance(v, tuple) else v)
            peak = _price("peak") if by_tier["peak"] else None
            shoulder = _price("shoulder") if by_tier["shoulder"] else None
            offpeak = _price("offpeak") if by_tier["offpeak"] else None
            out["peak_rate_per_kwh"] = peak
            out["shoulder_rate_per_kwh"] = shoulder
            out["offpeak_rate_per_kwh"] = offpeak
            present = [v for v in (peak, shoulder, offpeak) if v is not None]
            out["rate_tou_mismatch"] = len(set(present)) > 1
 # A flat usage_rate_per_kwh is only ever valid here when every
 # tier this bill shows happens to be numerically equal (this
 # account's own real history 2022-04-25 through 2026-09-06) -
 # otherwise there's no single correct flat number to report, so
 # this is left None rather than silently picking one tier
 # (matches the convention already established for the real
 # 2026-09-07 "tou" period in agl_tariff_rates.json).
            out["usage_rate_per_kwh"] = present[0] if len(set(present)) == 1 else None
        elif len(tiers_found) == 1:
 # Only one of Peak/Off-peak/Shoulder actually printed on this
 # bill - nothing to differentiate it against, so it's a flat
 # rate for this period regardless of which single tier label
 # AGL's own bill happened to print (see docstring above).
            only = by_tier[tiers_found[0]][0]
            out["usage_rate_per_kwh"] = float(only[0] if isinstance(only, tuple) else only)
        elif by_tier["other"]:
 # REQ-126: the oldest pre-TOU bills have no Peak/Off-peak/
 # Shoulder split at all - just a single flat "General Usage"
 # row. Fall back to that row by its own label rather than
 # guessing which "other" row is the real usage rate (a
 # Controlled Load row is also kWh and must NOT be mistaken
 # for it).
            general_rows = [v for v in by_tier["other"] if "general usage" in v[1]]
            if general_rows:
                out["usage_rate_per_kwh"] = float(general_rows[0][0])
        days_rows = [r for r in rows if r[3] == "days"]
        if days_rows:
            out["supply_charge_per_day"] = float(days_rows[0][4])
        return out

    def extract_rates(self, text: str) -> Dict[str, Any]:
        """REQ-112: pulls the actual dated TARIFF RATES off an AGL bill's
        own "New charges and credits" table - the CSV usage-export never
        carries this, only the bill PDF does (a household's dollars-per-
        kWh, feed-in tariff, demand rate and daily supply charge are
        plan/pricing facts, not meter readings). Every regex here was
        built and verified against real bills spanning multiple real rate
        changes (see AGENTS.local.md REQ-031/REQ-112) before being
        trusted, per REQ-031's own hard-won lesson about narrow/guessed
        regexes silently missing a real line on the bill.

        A bill whose OWN billing period straddles a real rate change
        prints TWO full usage/supply sub-tables (each its own Peak/
        Off-peak/Shoulder/Supply-charge set), separated by an
        "Energy plan & price change - <date> to <date> (<N> days)"
        marker that names the date range the FOLLOWING sub-table covers -
        confirmed real on the actual 2026-06-30 transition bill, which
        also isolates its Demand line into its own separate 1-day
        sub-block between the two usage tables. Blindly taking "the
        first 3 kWh rows" on a bill like that would silently return the
        SUPERSEDED rate as if it were this bill's own - exactly the kind
        of silent-miss REQ-031 already warned about once, so this is
        handled explicitly rather than assumed away.

        Returns:
          - blocks: list of {date_from, date_to (YYYY-MM-DD strings or
            None if unknown), usage_rate_per_kwh, rate_tou_mismatch,
            supply_charge_per_day, solar_feed_in_tier1_rate_per_kwh,
            solar_feed_in_tier2_rate_per_kwh} - normally ONE entry (the
            whole bill), two only for a genuine straddling bill. A block
            with no usage/supply rows found (nothing to report) is
            omitted rather than returned empty.
          - demand_rate_per_kw_day / demand_confirmed: bool - kept
            OUTSIDE the per-block split deliberately: the one real
            straddling bill seen puts its Demand line in its own
            isolated sub-block whose date range doesn't line up with
            either usage block, so this repo doesn't guess which side of
            a split it belongs to - it's reported once for the bill and
            `demand_period_ambiguous` is set True when a split was
            detected, so a caller can decide whether to trust it as-is.
          - warnings: list of str - anything found but not confidently
            attributable (e.g. a solar-tier occurrence count that didn't
            match the number of usage blocks), surfaced rather than
            silently guessed.
        """
        out: Dict[str, Any] = {
            "blocks": [],
            "demand_rate_per_kw_day": None,
            "demand_confirmed": False,
            "demand_period_ambiguous": False,
            "warnings": [],
        }

 # Bound the search to the actual usage/supply table - from its own
 # header down to "Total charges" - so a coincidental "$X $Y" elsewhere
 # on the bill (e.g. in "Previous balance and payments") can't be
 # mistaken for a rate row.
        table_m = re.search(
            r"Usage\s*and\s*supply\s*charges.*?Total\s*charges\s*\+",
            text, re.IGNORECASE | re.DOTALL,
        )
        table_text = table_m.group(0) if table_m else ""

 # Demand is searched GLOBALLY (not per-block) - see docstring above
 # for why: its own sub-block boundary doesn't reliably line up with
 # either usage rate block on the one real straddling bill checked.
        demand_m = self._RATE_ROW_RE.search(
            "\n".join(ln for ln in table_text.splitlines() if " kW " in ln or ln.strip().endswith("kW"))
            or table_text
        )
 # The line-filter above is a best-effort narrowing; fall back to a
 # direct scan for the one row shaped "<n> kW $<price> $<amount>"
 # (never "kWh") if it didn't isolate anything.
        for r in self._find_rate_rows(table_text):
            if r[1] == "kW":
                out["demand_rate_per_kw_day"] = float(r[2])
                out["demand_confirmed"] = True
                break

        markers = list(self._PLAN_CHANGE_RE.finditer(table_text))
        raw_blocks = []
        if not markers:
            raw_blocks.append((table_text, None, None))
        else:
            out["demand_period_ambiguous"] = True
            raw_blocks.append((table_text[:markers[0].start()], None, None))
            for i, m in enumerate(markers):
                seg_end = markers[i + 1].start() if i + 1 < len(markers) else len(table_text)
                seg_text = table_text[m.end():seg_end]
                raw_blocks.append((seg_text, m.group(1), m.group(2)))

        usage_blocks = []
        for seg_text, date_from, date_to in raw_blocks:
            block = self._extract_block_rates(seg_text)
            if block["usage_rate_per_kwh"] is None and block["supply_charge_per_day"] is None:
                continue  # e.g. the isolated demand-only sub-block - nothing to report
            block["date_from"] = self._parse_flexible_date(date_from) if date_from else None
            block["date_to"] = self._parse_flexible_date(date_to) if date_to else None
            block["solar_feed_in_tier1_rate_per_kwh"] = None
            block["solar_feed_in_tier2_rate_per_kwh"] = None
            usage_blocks.append(block)

 # Solar credit rows can ALSO repeat once per rate change on a
 # straddling bill (confirmed real: two "Standard feed-in tariff*"
 # lines at different rates on the one split bill checked) - findall,
 # not search, and map matches to blocks IN DOCUMENT ORDER, which is
 # also chronological order on every real bill checked (old rate
 # listed before new rate).
        tier1_matches = [float(x) for x in re.findall(
            r"Standard\s*feed-in\s*tariff\*?\s*At\s*all\s*times\s*[\d,.]+\s*kWh\s+\$([\d.]+)",
            text, re.IGNORECASE,
        )]
 # REQ-126: pre-2023 bills print the credit row amount-first with no
 # "At all times" phrase at all - e.g. "$231.68cr1103.235kWh $0.21
 # Standard Feed-in Tariff*" (see _OLD_ROW_RE's docstring for the same
 # amount-first pattern in the usage/supply table above). Only
 # searched when the new-layout pattern found nothing, since a bill
 # can only be one layout or the other, never both.
        if not tier1_matches:
            tier1_matches = [float(x) for x in re.findall(
                r"\$[\d,]+\.\d{2}cr[\d,]+\.?\d*kWh\s*\$([\d.]+)(?:Standard\s*)?Feed-in\s*Tariff",
                text, re.IGNORECASE,
            )]
        tier2_matches = [float(x) for x in re.findall(
            r"\bNext\*?\s*At\s*all\s*times\s*[\d,.]+\s*kWh\s+\$([\d.]+)",
            text, re.IGNORECASE,
        )]

        def _assign(matches, field_name):
            if not matches:
                return
            if len(matches) == 1:
                for b in usage_blocks:
                    b[field_name] = matches[0]
            elif len(matches) == len(usage_blocks):
                for b, v in zip(usage_blocks, matches):
                    b[field_name] = v
            else:
                out["warnings"].append(
                    f"{field_name}: found {len(matches)} occurrence(s) but {len(usage_blocks)} "
                    f"usage block(s) - couldn't confidently attribute, left unset (manual check needed)."
                )

        _assign(tier1_matches, "solar_feed_in_tier1_rate_per_kwh")
        _assign(tier2_matches, "solar_feed_in_tier2_rate_per_kwh")

        out["blocks"] = usage_blocks
        return out

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        # REQ-117 (2026-09-24): filenames now present the billing PERIOD the
        # bill is actually about (e.g. "20260725_to_20260824"), not the date
        # the bill was issued - issue_date told you nothing about which month
        # the charges were for, which is exactly what made the AGL invoice
        # folder hard to scan by eye. Falls back to issue_date only for the
        # handful of pre-2015 bills whose layout has no parseable "Bill
        # period:" line at all (extract()'s period_start/period_end are None).
        svc = data.get("service_type") or "electricity"
        period_start = data.get("period_start")
        period_end = data.get("period_end")
        if period_start and period_end:
            clean_start = period_start.replace("-", "")
            clean_end = period_end.replace("-", "")
            return f"Statement_{svc}_{clean_start}_to_{clean_end}.pdf"
        dt_str = data.get("issue_date")
        if dt_str:
            clean_dt = dt_str.replace("-", "")
            return f"Statement_{svc}_{clean_dt}.pdf"
        return os.path.basename(original_path)

    def resolve_target_dir(self, data: Dict[str, Any], original_path: str) -> Optional[str]:
        # BUG-131 follow-up (2026-09-21): the hardcoded iCloud fallback
        # this used to have (duplicated independently in sync_invoices.py
        # and providers_power/power_base.py) is gone - config.json's
        # directories.power_invoices is genuinely optional now; if unset,
        # a renamed AGL PDF is simply left beside its original file.
        try:
            from lib.tessie_api_common import load_config
            cfg = load_config()
            agl_dir = cfg.get("directories", {}).get("power_invoices")
            if agl_dir:
                return os.path.abspath(os.path.expanduser(agl_dir))
        except Exception:
            pass
        return os.path.dirname(original_path)


# ------------------------------------------------------------------------------
# 2.2 TESLA SUPERCHARGING INVOICE EXTRACTOR
# ------------------------------------------------------------------------------

class TeslaInvoiceExtractor(BaseInvoiceExtractor):
    """
    Tesla Supercharging Invoice Extractor.
    Extracts Invoice Number (2010P...), Session Date & Time, Location,
    Energy kWh, Unit Rate $/kWh, GST, and Total AUD.
    Formats filename as: {YYYYMMDD}_{HHMM}_Tesla_{InvoiceNo}_{Place}.pdf
    """
    name = "tesla"
    display_name = "Tesla Supercharging"

    def detect(self, text: str, pdf_path: str) -> bool:
        t_low = text.lower()
        fname = os.path.basename(pdf_path)
        if "tesla motors australia" in t_low or "supercharging" in t_low or "www.tesla.com" in t_low:
            return True
        if re.search(r"2010P\d{10}", text) or re.search(r"TSLA-[A-Za-z0-9\-]+", text):
            return True
        if re.match(r"^\d{8}_\d{4}_Tesla_", fname):
            return True
        return False

    def extract(self, text: str, pdf_path: str) -> Dict[str, Any]:
        fname = os.path.basename(pdf_path)
        data: Dict[str, Any] = {
            "provider": self.name,
            "provider_display": self.display_name,
            "network": "Tesla",
            "source_file": fname,
            "source_path": pdf_path,
            "invoice_number": None,
            "issue_date": None,
            "session_time": "0000",
            "location_name": None,
            "place_safe": "Supercharger",
            "energy_kwh": None,
            "unit_rate": None,
            "total_amount": None,
            "gst_amount": None,
            "vin": None,
            "charger_sn": None,
        }

 # 1. Invoice Number (e.g. 2010P0004164366)
        inv_m = re.search(r"(?:Invoice\s*Number\s*|TAX\s*INVOICE\s*)(2010P\d{10}|TSLA-[A-Za-z0-9\-]+)", text, re.IGNORECASE)
        if inv_m:
            data["invoice_number"] = inv_m.group(1).strip()
        else:
            sc_m = re.search(r"(2010P\d{10})", text)
            if sc_m:
                data["invoice_number"] = sc_m.group(1)

 # 2. Date
        dt_m = re.search(r"(?:Invoice\s*date|Date\s*of\s*Event|Date)\s*(\d{4}[\/\-]\d{2}[\/\-]\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})", text, re.IGNORECASE)
        if dt_m:
            data["issue_date"] = self._parse_flexible_date(dt_m.group(1))

 # Check existing filename for date and time: e.g. 20260606_2204_Tesla_...
        fn_match = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{4})_Tesla", fname)
        if fn_match:
            if not data["issue_date"]:
                data["issue_date"] = f"{fn_match.group(1)}-{fn_match.group(2)}-{fn_match.group(3)}"
            data["session_time"] = fn_match.group(4)
        elif not data["issue_date"]:
            fn_m2 = re.search(r"(\d{4})(\d{2})(\d{2})", fname)
            if fn_m2:
                data["issue_date"] = f"{fn_m2.group(1)}-{fn_m2.group(2)}-{fn_m2.group(3)}"

 # 3. Location
        loc_m = re.search(r"Charging\s*Location\s*([A-Za-z\s,\-]+?)(?:Talavera|Manns|Parraweena|S\/N|\d{4}\s+North Ryde|\d{4}\s+West Gosford|\d{4}\s+Miranda|\n|$)", text, re.IGNORECASE)
        if loc_m:
            data["location_name"] = loc_m.group(1).strip()
            data["place_safe"] = self._clean_place_name(data["location_name"])
        else:
            for site in ["Macquarie", "West Gosford", "Gosford", "Miranda", "Broadway", "Campbelltown", "Kirrawee"]:
                if site.lower() in text.lower():
                    data["location_name"] = site
                    data["place_safe"] = self._clean_place_name(site)
                    break

 # Check invoice_locations.json if available
        self._enrich_from_invoice_locations(data)

 # 4. Energy (kWh) & Unit Rate ($/kWh)
        ef_m = re.search(r"Energy\s*fee\s*([\d\.]+)\s*\/\s*kWh\s*([\d\.]+)\s*kWh", text, re.IGNORECASE)
        if ef_m:
            try:
                data["unit_rate"] = float(ef_m.group(1))
                data["energy_kwh"] = float(ef_m.group(2))
            except Exception:
                pass

        if data["energy_kwh"] is None:
            kwh_m = re.search(r"([\d\.]+)\s*kWh", text, re.IGNORECASE)
            if kwh_m:
                try:
                    data["energy_kwh"] = float(kwh_m.group(1))
                except Exception:
                    pass

 # 5. Amounts (AUD)
        tot_m = re.search(r"Total\s*Amount\s*\(AUD\)\s*([\d\.]+)", text, re.IGNORECASE)
        if not tot_m:
            tot_m = re.search(r"(?:Total\s*due|Total\s*incl\.?\s*GST)\s*AUD\s*\$?([\d\.]+)", text, re.IGNORECASE)
        if not tot_m:
 # Table row total (exclude date patterns like 2026/06/06)
            tot_m = re.search(r"Total\s*\(AUD\)\s*(?!\d{4}[\/\-])([\d\.]+)", text, re.IGNORECASE)
        if tot_m:
            try:
                data["total_amount"] = float(tot_m.group(1))
            except Exception:
                pass

        gst_m = re.search(r"Total\s*GST\s*([\d\.]+)|GST\s*\(10%\)\s*([\d\.]+)", text, re.IGNORECASE)
        if gst_m:
            try:
                data["gst_amount"] = float(gst_m.group(1) or gst_m.group(2))
            except Exception:
                pass

 # 6. VIN & Serial Number
        vin_m = re.search(r"Vehicle\s*Identification\s*Number:\s*([A-Za-z0-9]{17})", text, re.IGNORECASE)
        if vin_m:
            data["vin"] = vin_m.group(1)

        sn_m = re.search(r"S\/N:\s*([A-Za-z0-9\-\s\/]+?)(?:Vehicle|\n)", text, re.IGNORECASE)
        if sn_m:
            data["charger_sn"] = sn_m.group(1).strip()

        return data

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        fname = os.path.basename(original_path)
        if re.match(r"^\d{8}_\d{4}_Tesla_", fname):
            return fname

        dt_str = data.get("issue_date")
        tm_str = data.get("session_time") or "0000"
        inv_no = data.get("invoice_number") or "Invoice"
        place = data.get("place_safe") or "Supercharger"

        if dt_str:
            clean_dt = dt_str.replace("-", "")
            clean_tm = tm_str.replace(":", "")[:4]
            return f"{clean_dt}_{clean_tm}_Tesla_{inv_no}_{place}.pdf"
        return fname

    def resolve_target_dir(self, data: Dict[str, Any], original_path: str) -> Optional[str]:
        try:
            from lib.tessie_api_common import load_config
            cfg = load_config()
            inv_dir = cfg.get("directories", {}).get("invoices")
            if inv_dir:
                return os.path.abspath(os.path.expanduser(inv_dir))
        except Exception:
            pass
        default_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/charging_invoices")
        if os.path.isdir(default_dir):
            return default_dir
        return os.path.dirname(original_path)

    def _enrich_from_invoice_locations(self, data: Dict[str, Any]):
        """Load invoice_locations.json and align place name if matched."""
        inv_no = data.get("invoice_number")
        if not inv_no:
            return
        loc_json = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/charging_invoices/invoice_locations.json")
        if os.path.isfile(loc_json):
            try:
                import json
                with open(loc_json, "r") as f:
                    meta = json.load(f)
                rec = meta.get(inv_no)
                if rec and rec.get("place_name"):
                    data["place_safe"] = self._clean_place_name(rec["place_name"])
            except Exception:
                pass


# ------------------------------------------------------------------------------
# 2.3 EXPLOREN EV INVOICE & RECEIPT EXTRACTOR
# ------------------------------------------------------------------------------

class ExplorenInvoiceExtractor(BaseInvoiceExtractor):
    """
    Exploren EV Fast Charging Receipt & Tax Invoice Extractor.
    Extracts Receipt/Invoice No (e.g. 0000533887), Session Started DateTime,
    Location (e.g. Bunnings Rydalmere), Energy kWh, Unit Rate, Total AUD, and GST.
    Formats filename as: {YYYYMMDD}_{HHMM}_Exploren_{InvoiceNo}_{Place}.pdf
    """
    name = "exploren"
    display_name = "Exploren EV"

    def detect(self, text: str, pdf_path: str) -> bool:
        t_low = text.lower()
        fname = os.path.basename(pdf_path)
        if "exploren" in t_low or "exploren" in fname.lower():
            return True
        if "schneider 120kw dc" in t_low or "bunnings rydalmere" in t_low or "bunnings gladesville" in t_low:
            return True
        if re.search(r"Receipt\s*No\.?\s*0000\d{6}", text):
            return True
        if re.match(r"^\d{8}_\d{4}_Exploren_", fname):
            return True
        return False

    def extract(self, text: str, pdf_path: str) -> Dict[str, Any]:
        fname = os.path.basename(pdf_path)
        data: Dict[str, Any] = {
            "provider": self.name,
            "provider_display": self.display_name,
            "network": "Exploren",
            "source_file": fname,
            "source_path": pdf_path,
            "invoice_number": None,
            "issue_date": None,
            "session_time": "0000",
            "location_name": None,
            "place_safe": "Exploren_Station",
            "charge_point": None,
            "energy_kwh": None,
            "unit_rate": None,
            "total_amount": None,
            "gst_amount": None,
            "transaction_ref": None,
        }

 # 1. Receipt / Invoice Number (e.g. 0000533887)
        rec_m = re.search(r"(?:Receipt\s*No\.?\s*|Invoice\s*#?\s*)([0-9]{8,12})", text, re.IGNORECASE)
        if rec_m:
            data["invoice_number"] = rec_m.group(1)
        else:
            fn_inv = re.search(r"Exploren_([0-9]{8,12})", fname)
            if fn_inv:
                data["invoice_number"] = fn_inv.group(1)

 # 2. Session Start Date & Time
        start_m = re.search(r"Session\s*started\s*(\d{1,2}/\d{1,2}/\d{4})\s*(\d{1,2}:\d{2}(?::\d{2})?\s*[APap][Mm])", text, re.IGNORECASE)
        if start_m:
            data["issue_date"] = self._parse_flexible_date(start_m.group(1))
 # Format time to 24h HHMM
            try:
                tm_dt = datetime.datetime.strptime(start_m.group(2).strip(), "%I:%M:%S %p" if ":" in start_m.group(2).strip() and start_m.group(2).count(":") == 2 else "%I:%M %p")
                data["session_time"] = tm_dt.strftime("%H%M")
            except Exception:
                pass
        else:
            dt_m = re.search(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})\s*(\d{1,2}:\d{2}(?::\d{2})?\s*[APap][Mm])", text, re.IGNORECASE)
            if dt_m:
                data["issue_date"] = self._parse_flexible_date(dt_m.group(1))
                try:
                    tm_dt = datetime.datetime.strptime(dt_m.group(2).strip(), "%I:%M:%S %p" if dt_m.group(2).count(":") == 2 else "%I:%M %p")
                    data["session_time"] = tm_dt.strftime("%H%M")
                except Exception:
                    pass

 # Fallback date from existing filename: e.g. 20260530_1614_Exploren_...
        fn_match = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{4})_Exploren", fname)
        if fn_match:
            if not data["issue_date"]:
                data["issue_date"] = f"{fn_match.group(1)}-{fn_match.group(2)}-{fn_match.group(3)}"
            if data["session_time"] == "0000":
                data["session_time"] = fn_match.group(4)

 # 3. Location & Charge Point
        loc_m = re.search(r"Location\s*([^\n\r]+?)\s*Charge\s*Point", text, re.IGNORECASE)
        if loc_m:
            data["location_name"] = loc_m.group(1).strip()
            data["place_safe"] = self._clean_place_name(data["location_name"])
        else:
            for site in ["Bunnings Rydalmere", "Bunnings Gladesville", "Rydalmere", "Gladesville"]:
                if site.lower() in text.lower():
                    data["location_name"] = site
                    data["place_safe"] = self._clean_place_name(site)
                    break

        cp_m = re.search(r"Charge\s*Point\s*([^\n\r]+?)\s*Connector\s*ID", text, re.IGNORECASE)
        if cp_m:
            data["charge_point"] = cp_m.group(1).strip()

 # Check invoice_locations.json if available
        self._enrich_from_invoice_locations(data)

 # 4. Energy (kWh) & Unit Price ($/kWh)
        kwh_m = re.search(r"Total\s*energy\s*([\d\.]+)\s*kWh", text, re.IGNORECASE)
        if kwh_m:
            try:
                data["energy_kwh"] = float(kwh_m.group(1))
            except Exception:
                pass

        rate_m = re.search(r"Unit\s*price\s*(?:net|QTY)?\s*\$([\d\.]+)\s*\$([\d\.]+)", text, re.IGNORECASE)
        if rate_m:
            try:
                data["unit_rate"] = float(rate_m.group(2)) # unit price gross incl tax
            except Exception:
                pass

 # 5. Amounts
        amt_m = re.search(r"(?:Total\s*incl\.?\s*Tax|Total\s*for\s*payment|Line\s*total)\s*\$([\d\.]+)", text, re.IGNORECASE)
        if amt_m:
            try:
                data["total_amount"] = float(amt_m.group(1))
            except Exception:
                pass

        gst_m = re.search(r"Tax\s*10%\s*\$([\d\.]+)", text, re.IGNORECASE)
        if gst_m:
            try:
                data["gst_amount"] = float(gst_m.group(1))
            except Exception:
                pass

 # 6. Transaction Reference
        tx_m = re.search(r"Transaction\s*Ref\.?\s*([A-Za-z0-9_]+)", text, re.IGNORECASE)
        if tx_m:
            data["transaction_ref"] = tx_m.group(1)

        return data

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        fname = os.path.basename(original_path)
        if re.match(r"^\d{8}_\d{4}_Exploren_", fname):
            return fname

        dt_str = data.get("issue_date")
        tm_str = data.get("session_time") or "0000"
        inv_no = data.get("invoice_number") or "Receipt"
        place = data.get("place_safe") or "Exploren"

        if dt_str:
            clean_dt = dt_str.replace("-", "")
            clean_tm = tm_str.replace(":", "")[:4]
            return f"{clean_dt}_{clean_tm}_Exploren_{inv_no}_{place}.pdf"
        return fname

    def resolve_target_dir(self, data: Dict[str, Any], original_path: str) -> Optional[str]:
        try:
            from lib.tessie_api_common import load_config
            cfg = load_config()
            inv_dir = cfg.get("directories", {}).get("invoices")
            if inv_dir:
                return os.path.abspath(os.path.expanduser(inv_dir))
        except Exception:
            pass
        default_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/charging_invoices")
        if os.path.isdir(default_dir):
            return default_dir
        return os.path.dirname(original_path)

    def _enrich_from_invoice_locations(self, data: Dict[str, Any]):
        inv_no = data.get("invoice_number")
        if not inv_no:
            return
        loc_json = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/charging_invoices/invoice_locations.json")
        if os.path.isfile(loc_json):
            try:
                import json
                with open(loc_json, "r") as f:
                    meta = json.load(f)
                rec = meta.get(inv_no)
                if rec and rec.get("place_name"):
                    data["place_safe"] = self._clean_place_name(rec["place_name"])
            except Exception:
                pass


# ------------------------------------------------------------------------------
# 2.4 CHARGEFOX INVOICE EXTRACTOR
# ------------------------------------------------------------------------------

class ChargefoxInvoiceExtractor(BaseInvoiceExtractor):
    """
    Chargefox EV Charging Invoice Extractor.
    Extracts Invoice Number, Session DateTime, Location, Energy kWh, and Total AUD.
    Formats filename as: {YYYYMMDD}_{HHMM}_Chargefox_{InvoiceNo}_{Place}.pdf
    """
    name = "chargefox"
    display_name = "Chargefox"

    def detect(self, text: str, pdf_path: str) -> bool:
        t_low = text.lower()
        fname = os.path.basename(pdf_path)
        if "chargefox" in t_low or "chargefox" in fname.lower():
            return True
        if re.search(r"CF-\d+", text):
            return True
        if re.match(r"^\d{8}_\d{4}_Chargefox_", fname):
            return True
        return False

    def extract(self, text: str, pdf_path: str) -> Dict[str, Any]:
        fname = os.path.basename(pdf_path)
        data: Dict[str, Any] = {
            "provider": self.name,
            "provider_display": self.display_name,
            "network": "Chargefox",
            "source_file": fname,
            "source_path": pdf_path,
            "invoice_number": None,
            "issue_date": None,
            "session_time": "0000",
            "location_name": None,
            "place_safe": "Chargefox_Station",
            "energy_kwh": None,
            "unit_rate": None,
            "total_amount": None,
            "gst_amount": None,
        }

 # 1. Invoice Number
        cf_m = re.search(r"(?:Invoice\s*#?\s*|TAX\s*INVOICE\s*)(CF-[0-9]+|[0-9]{6,12})", text, re.IGNORECASE)
        if cf_m:
            data["invoice_number"] = cf_m.group(1)

 # 2. Date & Time
        dt_m = re.search(r"(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\s*(\d{1,2}:\d{2}(?::\d{2})?\s*[APap][Mm])?", text, re.IGNORECASE)
        if dt_m:
            data["issue_date"] = self._parse_flexible_date(dt_m.group(1))
            if dt_m.group(2):
                try:
                    tm_dt = datetime.datetime.strptime(dt_m.group(2).strip(), "%I:%M:%S %p" if dt_m.group(2).count(":") == 2 else "%I:%M %p")
                    data["session_time"] = tm_dt.strftime("%H%M")
                except Exception:
                    pass

 # 3. Location
        loc_m = re.search(r"(?:Station|Location|Site)[:\s]*([^\n\r]+)", text, re.IGNORECASE)
        if loc_m:
            data["location_name"] = loc_m.group(1).strip()
            data["place_safe"] = self._clean_place_name(data["location_name"])

 # 4. Energy & Amount
        kwh_m = re.search(r"([\d\.]+)\s*kWh", text, re.IGNORECASE)
        if kwh_m:
            try:
                data["energy_kwh"] = float(kwh_m.group(1))
            except Exception:
                pass

        amt_m = re.search(r"Total\s*(?:incl\.?\s*GST|Amount)?[\s:]*\$([0-9,]+\.\d{2})", text, re.IGNORECASE)
        if amt_m:
            try:
                data["total_amount"] = float(amt_m.group(1).replace(",", ""))
            except Exception:
                pass

        return data

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        fname = os.path.basename(original_path)
        if re.match(r"^\d{8}_\d{4}_Chargefox_", fname):
            return fname

        dt_str = data.get("issue_date")
        tm_str = data.get("session_time") or "0000"
        inv_no = data.get("invoice_number") or "Invoice"
        place = data.get("place_safe") or "Chargefox"

        if dt_str:
            clean_dt = dt_str.replace("-", "")
            clean_tm = tm_str.replace(":", "")[:4]
            return f"{clean_dt}_{clean_tm}_Chargefox_{inv_no}_{place}.pdf"
        return fname

    def resolve_target_dir(self, data: Dict[str, Any], original_path: str) -> Optional[str]:
        default_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/charging_invoices")
        if os.path.isdir(default_dir):
            return default_dir
        return os.path.dirname(original_path)


# ------------------------------------------------------------------------------
# 2.5 COMPOSITE EV CHARGING INVOICE EXTRACTOR (ROUTER)
# ------------------------------------------------------------------------------

class TeslaChargingInvoiceExtractor(BaseInvoiceExtractor):
    """
    Composite EV Charging Invoice Router & Extractor.
    Automatically identifies and routes between Tesla, Exploren, Chargefox,
    Evie, BP pulse, AmpCharge, EVX, and generic EV charging receipts.
    """
    name = "charging"
    display_name = "EV Charging"

    def detect(self, text: str, pdf_path: str) -> bool:
        t_low = text.lower()
        fname = os.path.basename(pdf_path)
        if any(k in t_low for k in ["supercharging", "tesla motors", "exploren", "chargefox", "evie networks", "bp pulse", "ampcharge", "schneider 120kw"]):
            return True
        if re.search(r"2010P\d{10}", text) or re.search(r"Receipt\s*No\.?\s*0000\d{6}", text):
            return True
        if re.match(r"^\d{8}_\d{4}_(Tesla|Exploren|Chargefox|Evie|BPPulse)", fname):
            return True
        return False

    def extract(self, text: str, pdf_path: str) -> Dict[str, Any]:
        t_low = text.lower()
        fname = os.path.basename(pdf_path)
        if "exploren" in t_low or "exploren" in fname.lower() or "schneider 120kw" in t_low:
            return ExplorenInvoiceExtractor().extract(text, pdf_path)
        elif "chargefox" in t_low or "chargefox" in fname.lower() or re.search(r"CF-\d+", text):
            return ChargefoxInvoiceExtractor().extract(text, pdf_path)
        else:
            return TeslaInvoiceExtractor().extract(text, pdf_path)

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        net = (data.get("network") or "").lower()
        if "exploren" in net:
            return ExplorenInvoiceExtractor().generate_filename(data, original_path)
        elif "chargefox" in net:
            return ChargefoxInvoiceExtractor().generate_filename(data, original_path)
        return TeslaInvoiceExtractor().generate_filename(data, original_path)

    def resolve_target_dir(self, data: Dict[str, Any], original_path: str) -> Optional[str]:
        return TeslaInvoiceExtractor().resolve_target_dir(data, original_path)


# ------------------------------------------------------------------------------
# 2.6 GENERIC INVOICE EXTRACTOR (FALLBACK)
# ------------------------------------------------------------------------------

class GenericInvoiceExtractor(BaseInvoiceExtractor):
    """Fallback extractor for unclassified PDFs."""
    name = "generic"
    display_name = "Generic PDF / Invoice"

    def detect(self, text: str, pdf_path: str) -> bool:
        return True

    def extract(self, text: str, pdf_path: str) -> Dict[str, Any]:
        fname = os.path.basename(pdf_path)
        data: Dict[str, Any] = {
            "provider": self.name,
            "provider_display": self.display_name,
            "source_file": fname,
            "source_path": pdf_path,
            "invoice_number": None,
            "issue_date": None,
            "total_amount": None,
        }

 # Generic date search
        dt_m = re.search(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", text)
        if dt_m:
            data["issue_date"] = self._parse_flexible_date(dt_m.group(1))

 # Generic dollar amount
        amt_m = re.search(r"\$([0-9,]+\.\d{2})", text)
        if amt_m:
            try:
                data["total_amount"] = float(amt_m.group(1).replace(",", ""))
            except ValueError:
                pass

        return data

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        return os.path.basename(original_path)


# ==============================================================================
# 3. REGISTRY & DISPATCH FUNCTIONS
# ==============================================================================

AVAILABLE_EXTRACTORS: List[BaseInvoiceExtractor] = [
    AGLInvoiceExtractor(),
    TeslaInvoiceExtractor(),
    ExplorenInvoiceExtractor(),
    ChargefoxInvoiceExtractor(),
    TeslaChargingInvoiceExtractor(),
    GenericInvoiceExtractor(),
]


def get_extractor(provider_name: Optional[str] = None) -> BaseInvoiceExtractor:
    """Return specific extractor or default to generic."""
    if provider_name:
        p_lower = provider_name.lower()
        for ext in AVAILABLE_EXTRACTORS:
            if ext.name.lower() == p_lower:
                return ext
    return GenericInvoiceExtractor()


def detect_and_extract_invoice(
    pdf_path: str, forced_provider: Optional[str] = None
) -> Tuple[BaseInvoiceExtractor, Dict[str, Any], str]:
    """
    Extract text, detect provider (or use forced), and return (extractor, metadata_dict, raw_text).
    """
    text = PDFTextExtractor.extract_text(pdf_path)

    if forced_provider and forced_provider.lower() != "auto":
        extractor = get_extractor(forced_provider)
    else:
        extractor = GenericInvoiceExtractor()
        for ext in AVAILABLE_EXTRACTORS:
            if ext.name != "generic" and ext.name != "charging" and ext.detect(text, pdf_path):
                extractor = ext
                break
        if extractor.name == "generic":
 # Check charging composite
            charging_ext = TeslaChargingInvoiceExtractor()
            if charging_ext.detect(text, pdf_path):
                extractor = charging_ext

    data = extractor.extract(text, pdf_path)
    return extractor, data, text
