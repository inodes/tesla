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

    def generate_filename(self, data: Dict[str, Any], original_path: str) -> str:
        svc = data.get("service_type") or "electricity"
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
