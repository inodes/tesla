#!/usr/bin/env python3
"""
Tessie Charging & Supercharger Reconciliation Engine
=====================================================
- Multi-Registry Place & Charger Resolver (superchargers.json, charging.json, places.json)
- Config File Support (config.json / Tessie/config.json / ~/.config/tesla/config.json)
- Custom Local Invoices Directory (Kept private outside repo)
- Tesla Supercharger PDF & CSV Tax Invoice Parser (Zero external dependencies)
- 3rd-Party Fast/AC Charging Parser & Network Identifier (Chargefox, Evie, BP Pulse, Jolt, etc.)
- Dispenser Meter vs Battery BMS Charging Efficiency Loss Calculator
- Time-of-Use (TOU) Rate Schedule & GST Auditor
- Charges Master Consolidator (charges_master.csv)
- Multi-Level Rich Terminal Reporting & Deep-Dive Session Inspector
- Export Reconciled Reports to CSV / JSON
"""

import os
import sys
import time

# Auto re-exec inside local direnv/pyenv virtual environment if not already active
_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_script_dir)
for _py_candidate in [
    os.path.join(_repo_root, ".direnv", "python-3.11", "bin", "python3"),
    os.path.join(_repo_root, ".direnv", "python-3.11", "bin", "python"),
    os.path.join(_repo_root, ".venv", "bin", "python3"),
    os.path.join(_repo_root, ".venv", "bin", "python")
]:
    if os.path.isfile(_py_candidate) and os.path.abspath(sys.executable) != os.path.abspath(_py_candidate):
        try:
            import pypdf
        except ImportError:
            try:
                _orig = getattr(sys, "orig_argv", None)
                if _orig and len(_orig) > 1:
                    os.execv(_py_candidate, [_py_candidate] + _orig[1:])
                else:
                    os.execv(_py_candidate, [_py_candidate] + sys.argv)
            except Exception:
                pass

import re
import csv
import json
import math
import zlib
import shutil
import tempfile
import signal
import argparse
import unicodedata

def clean_station_short_name(name: str, max_length: int = 80) -> str:
    """
    Normalizes station names into filesystem-safe, cross-platform identifiers:
    - Transliterates Unicode accents (e.g. 'Café' -> 'Cafe')
    - Strips or replaces non-alphanumeric characters with underscores
    - Condenses consecutive underscores and strips leading/trailing underscores
    - Enforces safe length limits and prevents empty string results
    """
    if not name:
        return "Station"
    s = unicodedata.normalize('NFKD', str(name))
    s = s.encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    s = s.strip("_")
    if max_length and len(s) > max_length:
        s = s[:max_length].rstrip("_")
    return s or "Station"

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except Exception:
    pass
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone
from collections import defaultdict

def resolve_location_timezone(state: str = None, country: str = None, lat: float = None, lon: float = None) -> str:
    """Deterministically resolves standard IANA timezone identifier for a station or place."""
    c_clean = (country or "Australia").lower().replace("+", " ").strip()
    st_clean = (state or "").upper().strip()

    if c_clean in ["australia", "au"]:
        if not st_clean and lat is not None and lon is not None:
            try:
                from find_tesla_chargers import state_from_coords
                resolved_st, _ = state_from_coords(lat, lon, country=country)
                if resolved_st:
                    st_clean = resolved_st.upper().strip()
            except Exception:
                pass

        if st_clean in ["NSW", "ACT"]:
            return "Australia/Sydney"
        elif st_clean == "VIC":
            return "Australia/Melbourne"
        elif st_clean == "QLD":
            return "Australia/Brisbane"
        elif st_clean == "SA":
            return "Australia/Adelaide"
        elif st_clean == "WA":
            return "Australia/Perth"
        elif st_clean == "TAS":
            return "Australia/Hobart"
        elif st_clean == "NT":
            return "Australia/Darwin"
        if lon is not None:
            if lon < 129.0:
                return "Australia/Perth"
            elif lon < 141.0:
                return "Australia/Adelaide"
            elif lat is not None and lat > -28.0:
                return "Australia/Brisbane"
            else:
                return "Australia/Sydney"
        return "Australia/Sydney"
    elif c_clean in ["new zealand", "nz"]:
        return "Pacific/Auckland"
    elif c_clean in ["japan", "jp"]:
        return "Asia/Tokyo"
    elif c_clean in ["hong kong", "hk"]:
        return "Asia/Hong_Kong"
    elif c_clean in ["singapore", "sg"]:
        return "Asia/Singapore"
    elif c_clean in ["united kingdom", "uk", "great britain"]:
        return "Europe/London"
    elif c_clean in ["united states", "usa", "us"]:
        us_tz_map = {
            "CA": "America/Los_Angeles", "WA": "America/Los_Angeles", "OR": "America/Los_Angeles", "NV": "America/Los_Angeles",
            "NY": "America/New_York", "NJ": "America/New_York", "MA": "America/New_York", "FL": "America/New_York",
            "TX": "America/Chicago", "IL": "America/Chicago", "CO": "America/Denver", "AZ": "America/Phoenix", "HI": "Pacific/Honolulu"
        }
        return us_tz_map.get(st_clean, "America/New_York")
    elif c_clean in ["germany", "de"]:
        return "Europe/Berlin"
    elif c_clean in ["france", "fr"]:
        return "Europe/Paris"
    return "UTC"

# -----------------------------------------------------------------------------
# Terminal Formatting & Unicode Helpers (wcwidth compatible)
# -----------------------------------------------------------------------------

_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from archive_naming import next_archive_path
from tessie_api_common import unique_stamped_path
from deepdive_charts import write_interactive_chart, try_open_in_browser

from table_formatter import (
    char_width,
    display_len,
    truncate_display,
    pad_display,
    format_row,
    format_title_line,
    format_box_line,
)

def shorten_display_path(p, max_len=40):
    if not p:
        return ""
    p_str = str(p)
    try:
        if p_str.startswith(_repo_root):
            rel = os.path.relpath(p_str, _repo_root)
            if not rel.startswith("..") and len(rel) < len(p_str):
                p_str = rel
    except Exception:
        pass
    home = os.path.expanduser("~")
    if p_str.startswith(home):
        p_str = "~" + p_str[len(home):]
    p_str = p_str.replace("~/Library/Mobile Documents/com~apple~CloudDocs/", "iCloud/")
    p_str = p_str.replace("~/Library/Mobile Documents/com~apple~CloudDocs", "iCloud")
    if max_len and len(p_str) > max_len:
        p_str = "…" + p_str[-(max_len - 1):]
    return p_str

def wrap_text_display(s, max_width, indent="    "):
    clean = re.sub(r"\033\[[0-9;]*m", "", s)
    if display_len(clean) <= max_width:
        return [s]
    words = [w for w in s.split(" ") if w]
    lines = []
    current = ""
    for w in words:
        if not current:
            prefix = indent if lines else "    "
            candidate = f"{prefix}{w}"
        else:
            candidate = f"{current} {w}"
        if display_len(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = f"{indent}{w}"
    if current:
        lines.append(current)
    return lines or [s]

# ANSI Colors
C_RESET   = "\033[0m"
C_BOLD    = "\033[1m"
C_DIM     = "\033[2m"
C_RED     = "\033[91m"
C_GREEN   = "\033[92m"
C_ORANGE  = "\033[38;5;214m"
C_YELLOW  = C_ORANGE
C_BLUE    = "\033[94m"
C_MAGENTA = "\033[95m"
C_CYAN    = "\033[96m"
C_WHITE   = "\033[97m"

def haversine_distance_m(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return float("inf")
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %I:%M:%S %p",
    "%Y-%m-%d %I:%M %p",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %I:%M:%S %p",
    "%Y/%m/%d %I:%M %p",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %I:%M %p",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y %I:%M:%S %p",
    "%d-%m-%Y %I:%M %p",
    "%d-%m-%Y",
    "%Y%m%d",
    "%d %b %Y %H:%M:%S",
    "%d %b %Y %H:%M",
    "%d %b %Y %I:%M:%S %p",
    "%d %b %Y %I:%M %p",
    "%d %b %Y",
    "%d %B %Y %H:%M:%S",
    "%d %B %Y %H:%M",
    "%d %B %Y %I:%M:%S %p",
    "%d %B %Y %I:%M %p",
    "%d %B %Y"
]

def parse_flexible_date(date_str):
    if not date_str:
        return None
    d_clean = str(date_str).strip()
    d_lower = d_clean.lower()
    now = datetime.now()
    
    if d_lower == "today":
        return datetime(now.year, now.month, now.day)
    elif d_lower == "yesterday":
        y = now - timedelta(days=1)
        return datetime(y.year, y.month, y.day)
    
    days_of_week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if d_lower in days_of_week:
        target_weekday = days_of_week.index(d_lower)
        curr_weekday = now.weekday()
        days_ago = (curr_weekday - target_weekday) % 7
        if days_ago == 0:
            days_ago = 7
        target_date = now - timedelta(days=days_ago)
        return datetime(target_date.year, target_date.month, target_date.day)

    d_clean = re.sub(r"\s+(?:AEST|AEDT|UTC|GMT|[A-Z]{3,4})$", "", d_clean, flags=re.IGNORECASE).strip()

    # Try ISO 8601 format (e.g. 2026-09-05T00:47:11Z)
    try:
        iso_clean = d_clean.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_clean)
    except Exception:
        pass

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(d_clean, fmt)
        except ValueError:
            pass
    return None

def clean_tokens(s):
    return set(re.findall(r"\w+", (s or "").lower()))

# -----------------------------------------------------------------------------
# Pure Python PDF & CSV Invoice Parser
# -----------------------------------------------------------------------------

class TeslaInvoiceParser:
    @staticmethod
    def extract_text_from_pdf(pdf_path):
        try:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            text = "\n".join([page.extract_text() or "" for page in reader.pages])
            if text.strip():
                return text
        except Exception:
            pass

        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                text = "\n".join([page.extract_text() or "" for page in pdf.pages])
                if text.strip():
                    return text
        except Exception:
            pass

        # Pure Python zero-dependency parser with ToUnicode CMap & Chromium PDF support
        try:
            with open(pdf_path, "rb") as f:
                content = f.read()

            objects = {}
            for m in re.finditer(rb"(\d+)\s+0\s+obj\s*(.*?)\s*endobj", content, re.DOTALL):
                objects[int(m.group(1))] = m.group(2)

            def get_stream(body):
                m = re.search(rb"stream[\r\n]+(.*?)[\r\n]+endstream", body, re.DOTALL)
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

            # 1. Parse font CMaps
            font_cmaps = {}
            for oid, body in objects.items():
                if b"/ToUnicode" in body:
                    tu_m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
                    if tu_m:
                        tu_id = int(tu_m.group(1))
                        tu_stream = get_stream(objects.get(tu_id, b"")).decode("latin1", errors="replace")
                        cmap = {}
                        for block in re.findall(r"beginbfchar(.*?)endbfchar", tu_stream, re.DOTALL):
                            for src, dst in re.findall(r"<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>", block):
                                s_val = int(src, 16)
                                dst_str = "".join(chr(int(dst[i:i+4], 16)) for i in range(0, len(dst), 4))
                                cmap[s_val] = dst_str

                        for block in re.findall(r"beginbfrange(.*?)endbfrange", tu_stream, re.DOTALL):
                            for s_hex, e_hex, d_hex in re.findall(r"<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>", block):
                                s_val = int(s_hex, 16)
                                e_val = int(e_hex, 16)
                                d_val = int(d_hex, 16)
                                for i in range(e_val - s_val + 1):
                                    cmap[s_val + i] = chr(d_val + i)
                            for s_hex, e_hex, arr_str in re.findall(r"<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>\s*\[(.*?)\]", block, re.DOTALL):
                                s_val = int(s_hex, 16)
                                dests = re.findall(r"<([0-9a-fA-F]+)>", arr_str)
                                for i, d_h in enumerate(dests):
                                    d_str = "".join(chr(int(d_h[j:j+4], 16)) for j in range(0, len(d_h), 4))
                                    cmap[s_val + i] = d_str
                        font_cmaps[oid] = cmap

            # 2. Map Font Resource names (/F1, /F7, etc.) to Font object IDs
            res_fonts = {}
            contents_obj_ids = []
            for oid, body in objects.items():
                if b"/Type /Page\n" in body or b"/Type /Page " in body or b"/Type /Page/" in body or b"/Type/Page" in body:
                    f_dict_m = re.search(rb"/Font\s*<<([^>]+)>>", body)
                    if f_dict_m:
                        for fname, fid in re.findall(rb"/([A-Za-z0-9_]+)\s+(\d+)\s+0\s+R", f_dict_m.group(1)):
                            res_fonts[fname.decode("latin1")] = int(fid)
                    c_m = re.search(rb"/Contents\s+(\d+)\s+0\s+R", body)
                    if c_m:
                        contents_obj_ids.append(int(c_m.group(1)))
                    c_arr_m = re.search(rb"/Contents\s*\[(.*?)\]", body)
                    if c_arr_m:
                        for cid in re.findall(rb"(\d+)\s+0\s+R", c_arr_m.group(1)):
                            contents_obj_ids.append(int(cid))

            if not contents_obj_ids:
                for oid in objects:
                    contents_obj_ids.append(oid)

            extracted_lines = []
            for cid in contents_obj_ids:
                st_data = get_stream(objects.get(cid, b"")).decode("latin1", errors="replace")
                if not st_data:
                    continue
                if "BT" in st_data:
                    for bt in re.findall(r"BT(.*?)ET", st_data, re.DOTALL):
                        curr_font = None
                        chars = []
                        tokens = re.findall(r"(/[A-Za-z0-9_]+\s+\d+(?:\.\d+)?\s+Tf|<[0-9a-fA-F]+>\s*Tj|\([^\)]*\)\s*Tj|\[.*?\]\s*TJ)", bt, re.DOTALL)
                        for tok in tokens:
                            tf_m = re.match(r"/([A-Za-z0-9_]+)\s+\d+", tok)
                            if tf_m:
                                curr_font = tf_m.group(1)
                                continue
                            tj_hex = re.match(r"<([0-9a-fA-F]+)>\s*Tj", tok)
                            if tj_hex:
                                hx = tj_hex.group(1)
                                fid = res_fonts.get(curr_font)
                                cmap = font_cmaps.get(fid, {})
                                for i in range(0, len(hx), 4):
                                    code = int(hx[i:i+4], 16)
                                    chars.append(cmap.get(code, chr(code) if code < 128 else ""))
                                continue
                            tj_lit = re.match(r"\((.*?)\)\s*Tj", tok)
                            if tj_lit:
                                chars.append(tj_lit.group(1))
                                continue
                            if tok.startswith("[") and tok.endswith("TJ"):
                                fid = res_fonts.get(curr_font)
                                cmap = font_cmaps.get(fid, {})
                                for hx, lit in re.findall(r"<([0-9a-fA-F]+)>|\((.*?)\)", tok):
                                    if hx:
                                        for i in range(0, len(hx), 4):
                                            code = int(hx[i:i+4], 16)
                                            chars.append(cmap.get(code, chr(code) if code < 128 else ""))
                                    elif lit:
                                        chars.append(lit)
                        line = "".join(chars).strip()
                        if line:
                            extracted_lines.append(line)
                else:
                    for line in st_data.splitlines():
                        if any(k in line for k in ["Supercharging", "Tesla", "Invoice", "kWh", "AUD", "GST", "Total", "Date:"]):
                            extracted_lines.append(line.strip())

            raw_text = content.decode("latin1", errors="ignore")
            for line in raw_text.splitlines():
                if any(k in line for k in ["INV-", "TSLA-", "Supercharging", "Macquarie", "Gosford", "Miranda"]):
                    extracted_lines.append(line.strip())

            return "\n".join(extracted_lines)
        except Exception:
            return ""

    @classmethod
    def parse_invoice_file(cls, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            text = cls.extract_text_from_pdf(file_path)
            return cls.parse_invoice_text(text, source_file=file_path)
        elif ext in [".csv", ".tsv", ".txt"]:
            return cls.parse_invoice_csv_or_text(file_path)
        return None

    @classmethod
    def parse_invoice_text(cls, text, source_file=""):
        if not text:
            return None

        text_clean = text.replace("\r", "\n")
        lines = [l.strip() for l in text_clean.split("\n") if l.strip()]
        
        # 1. Invoice Number
        inv_number = ""
        inv_match = re.search(r"(?:TAX INVOICE[:\s]*|Invoice\s*(?:Number|No\.?|#)[:\s]*|Receipt\s*(?:Number|No\.?|#)[:\s]*)\s*([A-Za-z0-9-]{5,30})", text_clean, re.IGNORECASE)
        if inv_match and inv_match.group(1).lower() not in ["tesla", "number", "tax", "motors", "australia"]:
            inv_number = inv_match.group(1).strip()
        
        if not inv_number:
            for idx, line in enumerate(lines):
                if line.lower() in ["invoice number", "tax invoice:", "tax invoice", "invoice no", "receipt #", "receipt no.", "receipt no", "receipt number"]:
                    if idx + 1 < len(lines):
                        cand = lines[idx + 1].strip()
                        if len(cand) >= 4 and not any(k in cand.lower() for k in ["date", "tesla", "reference"]):
                            inv_number = cand
                            break

        if not inv_number:
            m2 = re.search(r"\b(2010[A-Za-z0-9]{10,12}|TSLA-[A-Za-z0-9\-]+|INV-[A-Za-z0-9\-]+|CF-[0-9]+)\b", text_clean)
            if m2:
                inv_number = m2.group(1).strip()
            else:
                inv_number = os.path.splitext(os.path.basename(source_file))[0] if source_file else "INV-UNKNOWN"

        # 2. Date & Time
        charge_dt = None
        # Check for Session started / Started at first (most accurate for charging)
        for idx, line in enumerate(lines):
            if line.lower() in ["session started", "started at", "start time", "charge start"]:
                if idx + 1 < len(lines):
                    d_cand = lines[idx + 1].strip()
                    charge_dt = parse_flexible_date(d_cand)
                    if charge_dt:
                        break

        if not charge_dt:
            for idx, line in enumerate(lines):
                if line.lower() in ["invoice date", "date of event", "date:", "date", "session time"]:
                    if idx + 1 < len(lines):
                        d_cand = lines[idx + 1].strip()
                        charge_dt = parse_flexible_date(d_cand)
                        if charge_dt:
                            break

        if not charge_dt:
            date_pref_m = re.search(r"(?:Session started|Date of Event|Invoice date|Date|Time|Started At|Charge Date|Session Time)[:\s]*([0-9A-Za-z\/\-\.\s:]+?(?:AM|PM|am|pm)?)(?:\s+AEST|\s+AEDT|\s+UTC|\n|$)", text_clean, re.IGNORECASE)
            if date_pref_m:
                charge_dt = parse_flexible_date(date_pref_m.group(1).strip())
        
        if not charge_dt:
            patterns = [
                r"\b(\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)?)\b",
                r"\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)?)\b",
                r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)?)\b"
            ]
            for pat in patterns:
                for m in re.finditer(pat, text_clean, re.IGNORECASE):
                    parsed = parse_flexible_date(m.group(1).strip())
                    if parsed and 2015 <= parsed.year <= 2035:
                        charge_dt = parsed
                        break
                if charge_dt:
                    break

        # 3. Location / Station Name
        location_name = ""
        for idx, line in enumerate(lines):
            if line.lower() in ["charging location", "location", "station", "site"]:
                loc_parts = []
                for j in range(idx + 1, min(idx + 4, len(lines))):
                    if not lines[j].startswith(("S/N:", "Vehicle", "Date", "Description", "Sold To", "Energy", "Total", "Charge Point", "Connector", "Session")):
                        loc_parts.append(lines[j])
                if loc_parts:
                    location_name = ", ".join(loc_parts)
                    break

        if not location_name:
            loc_match = re.search(r"(?:Description:\s*Supercharging\s*[-–:]\s*|Supercharging\s*[-–:]\s*)([^\n\r]+)", text_clean, re.IGNORECASE)
            if loc_match:
                location_name = loc_match.group(1).strip()
            else:
                for kw in ["Macquarie", "West Gosford", "Gosford", "Miranda", "Broadway", "Campbelltown", "Kirrawee", "St Leonards"]:
                    if kw.lower() in text_clean.lower():
                        location_name = kw
                        break

        # 4. Energy Delivered (kWh) & Unit Rate
        energy_kwh = None
        unit_rate = None

        # Priority 1: Direct Tesla invoice item line pattern
        # e.g. "Energy fee 0.54 / kWh 27.8050" or "Energy fee 0.40 / kWh 14.1194 kWh 10 5.65"
        ef_match = re.search(r"Energy\s+fee\s+([\d\.]+)\s*\/\s*kWh\s+([\d\.]+)", text_clean, re.IGNORECASE)
        if ef_match:
            try:
                unit_rate = float(ef_match.group(1))
                energy_kwh = float(ef_match.group(2))
            except Exception:
                pass

        if energy_kwh is None:
            for idx, line in enumerate(lines):
                if "kwh" in line.lower() and "/" not in line and "per" not in line.lower():
                    # Only match numbers with decimals to avoid capturing standalone "10" (GST %)
                    val_m = re.search(r"(\d+\.\d+)", line)
                    if val_m:
                        try:
                            v = float(val_m.group(1))
                            if v > 0.5:
                                energy_kwh = v
                                break
                        except Exception:
                            pass
        if energy_kwh is None:
            kwh_matches = re.finditer(r"(\d+\.\d+)\s*(?:\n\s*)?kWh", text_clean, re.IGNORECASE)
            for km in kwh_matches:
                m_str = km.group(1)
                if not re.search(rf"{re.escape(m_str)}\s*(?:\/|per)\s*kWh", text_clean, re.IGNORECASE):
                    try:
                        v = float(m_str)
                        if v > 0.5:
                            energy_kwh = v
                            break
                    except Exception:
                        pass

        # 5. Total Cost
        total_cost = None
        for idx, line in enumerate(lines):
            if any(k in line.lower() for k in ["total amount (aud)", "total amount", "total aud", "total due", "total for payment", "total incl. tax", "total incl tax"]):
                if idx + 1 < len(lines):
                    val_s = re.sub(r"[^\d.]", "", lines[idx + 1])
                    try:
                        total_cost = float(val_s)
                        break
                    except Exception:
                        pass

        if total_cost is None:
            total_match = re.search(r"(?<!sub)(?:Total\s+Amount\s*\(AUD\)|Total\s+Amount|Total\s+AUD|Total\s+Due|Total\s+for\s+payment|Total\s+incl\.?\s*Tax|Total(?!\s*excl))[:\s\$]*([\d\.]+)", text_clean, re.IGNORECASE)
            total_cost = float(total_match.group(1)) if total_match else None

        # 6. GST
        gst = None
        for idx, line in enumerate(lines):
            if any(k in line.lower() for k in ["total gst", "gst (10%)", "gst:", "tax 10%"]):
                if idx + 1 < len(lines):
                    val_s = re.sub(r"[^\d.]", "", lines[idx + 1])
                    try:
                        gst = float(val_s)
                        break
                    except Exception:
                        pass
        if gst is None:
            gst_match = re.search(r"(?:Total GST|GST(?:\s*\(?10%\)?)?|Tax\s*10%)[:\s\$]*([\d\.]+)", text_clean, re.IGNORECASE)
            gst = float(gst_match.group(1)) if gst_match else None

        # 7. Unit Rate
        if unit_rate is None:
            rate_match = re.search(r"@?\s*\$?(\d+\.\d{2,4})\s*(?:\/\s*kWh|per\s*kWh)", text_clean, re.IGNORECASE)
            unit_rate = float(rate_match.group(1)) if rate_match else None

        if total_cost is not None and energy_kwh and energy_kwh > 0 and unit_rate is None:
            unit_rate = round(total_cost / energy_kwh, 2)

        # 8. VIN
        vin = ""
        for idx, line in enumerate(lines):
            if any(k in line.lower() for k in ["vehicle identification number", "vin:"]):
                if idx + 1 < len(lines):
                    v_cand = re.sub(r"[^A-HJ-NPR-Z0-9]", "", lines[idx + 1].strip())
                    if len(v_cand) == 17:
                        vin = v_cand
                        break
        if not vin:
            vin_match = re.search(r"(?:Vehicle Identification Number:?|VIN:?)[:\s]*([A-HJ-NPR-Z0-9]{17})", text_clean, re.IGNORECASE)
            vin = vin_match.group(1).strip() if vin_match else ""

        if not charge_dt and total_cost is None and energy_kwh is None:
            return None

        network = "Tesla Supercharger"
        emoji = "🔴"
        lower_all = (source_file + " " + text_clean).lower()
        if "exploren" in lower_all:
            network = "Exploren"
            emoji = "🔌"
        elif "chargefox" in lower_all or inv_number.startswith("CF-"):
            network = "Chargefox"
            emoji = "🔌"
        elif "evie" in lower_all or inv_number.startswith("EV-"):
            network = "Evie"
            emoji = "🔌"
        elif "bp pulse" in lower_all or ("bp" in lower_all and "pulse" in lower_all):
            network = "BP Pulse"
            emoji = "🔌"
        elif "ampcharge" in lower_all or "ampol" in lower_all:
            network = "AmpCharge"
            emoji = "🔌"
        elif "jolt" in lower_all:
            network = "Jolt"
            emoji = "🔌"
        elif "nrma" in lower_all:
            network = "NRMA"
            emoji = "🔌"

        # Compute gross unit rate if total_cost and energy_kwh are available
        gross_rate = None
        if total_cost is not None and energy_kwh and energy_kwh > 0:
            gross_rate = round(total_cost / energy_kwh, 2)

        return {
            "invoice_number": inv_number,
            "source_file": os.path.basename(source_file),
            "source_path": source_file,
            "date": charge_dt,
            "location_raw": location_name,
            "network": network,
            "emoji": emoji,
            "energy_kwh": energy_kwh,
            "unit_rate": unit_rate,
            "gross_rate": gross_rate if gross_rate is not None else unit_rate,
            "total_cost": total_cost,
            "gst": gst,
            "vin": vin,
            "raw_text_snippet": text_clean[:300].strip()
        }

    @classmethod
    def parse_invoice_csv_or_text(cls, file_path):
        records = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                sample = f.read(2048)
                f.seek(0)
                delim = "\t" if "\t" in sample and "," not in sample else ","
                reader = csv.DictReader(f, delimiter=delim)
                for row in reader:
                    date_val = None
                    for k in ["Date", "Started At", "Start Time", "Charge Date", "Timestamp", "DateTime", "Started"]:
                        if k in row and row[k]:
                            date_val = parse_flexible_date(row[k])
                            if date_val:
                                break
                    
                    energy_val = None
                    for k in ["Energy (kWh)", "kWh", "Energy Used", "Energy Delivered", "Volume", "Energy"]:
                        if k in row and row[k]:
                            try:
                                energy_val = float(re.sub(r"[^\d.]", "", row[k]))
                                break
                            except Exception:
                                pass
                    
                    cost_val = None
                    for k in ["Cost", "Total", "Amount", "Total Cost", "Total Amount ($)", "AUD", "Price"]:
                        if k in row and row[k]:
                            try:
                                cost_val = float(re.sub(r"[^\d.]", "", row[k]))
                                break
                            except Exception:
                                pass

                    rate_val = None
                    for k in ["Rate", "Cost Per kWh", "Price Per kWh", "Unit Price"]:
                        if k in row and row[k]:
                            try:
                                rate_val = float(re.sub(r"[^\d.]", "", row[k]))
                                break
                            except Exception:
                                pass
                    
                    loc_val = row.get("Location") or row.get("Station") or row.get("Site") or row.get("Charger") or ""
                    inv_val = row.get("Invoice") or row.get("Invoice Number") or row.get("Receipt #") or os.path.basename(file_path)

                    if not date_val and energy_val is None and cost_val is None:
                        continue

                    fname_lower = os.path.basename(file_path).lower()
                    network = "Tesla Supercharger"
                    emoji = "🔴"
                    if "chargefox" in fname_lower or str(inv_val).startswith("CF-") or "chargefox" in loc_val.lower():
                        network = "Chargefox"
                        emoji = "🔌"
                    elif "evie" in fname_lower or str(inv_val).startswith("EV-") or "evie" in loc_val.lower():
                        network = "Evie"
                        emoji = "🔌"
                    elif "bp pulse" in fname_lower or "bp" in fname_lower:
                        network = "BP Pulse"
                        emoji = "🔌"
                    elif "jolt" in fname_lower:
                        network = "Jolt"
                        emoji = "🔌"

                    records.append({
                        "invoice_number": inv_val,
                        "source_file": os.path.basename(file_path),
                        "source_path": file_path,
                        "date": date_val,
                        "location_raw": loc_val,
                        "network": network,
                        "emoji": emoji,
                        "energy_kwh": energy_val,
                        "unit_rate": rate_val or (round(cost_val / energy_val, 2) if cost_val and energy_val else None),
                        "total_cost": cost_val,
                        "gst": (round(cost_val / 11.0, 2) if cost_val else None),
                        "vin": row.get("VIN", ""),
                        "raw_text_snippet": str(row)
                    })
        except Exception:
            pass
        return records

ChargingInvoiceParser = TeslaInvoiceParser

# -----------------------------------------------------------------------------
# Main Charging Analyzer & Reconciliation Engine
# -----------------------------------------------------------------------------

class TessieChargingAnalyzer:
    def __init__(self, config_path=None, tessie_dir=None, invoices_dir=None, tolerance_mins=None, tolerance_kwh=None):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.repo_root = os.path.dirname(self.script_dir)
        self.icloud_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")
        
        # 1. Load configuration from config.json if available
        self.config_file = None
        self.config = self.load_config(config_path)

        cfg_reconcile = self.config.get("reconciliation", {}) if isinstance(self.config.get("reconciliation"), dict) else {}
        self.tolerance_mins = tolerance_mins if tolerance_mins is not None else cfg_reconcile.get("tolerance_mins", 45)
        self.tolerance_kwh = tolerance_kwh if tolerance_kwh is not None else cfg_reconcile.get("tolerance_kwh", 5.0)

        # 2. Discover Tessie directories (CLI > config.json > auto-discovery)
        cfg_tessie_dir = self.config.get("tessie_directory") or self.config.get("tessie_dir")
        if cfg_tessie_dir:
            cfg_tessie_dir = os.path.expanduser(cfg_tessie_dir)

        self.tessie_dirs = []
        candidates_tessie = [
            tessie_dir,
            cfg_tessie_dir,
            os.path.join(self.repo_root, "Tessie"),
            os.path.join(self.script_dir, "Tessie"),
            os.path.expanduser("~/iCloud/repos/tesla/Tessie"),
            self.icloud_dir
        ]
        seen_tessie_dirs = set()
        for d in candidates_tessie:
            try:
                if d and os.path.isdir(d):
                    real_d = os.path.abspath(os.path.realpath(d))
                    if real_d not in seen_tessie_dirs:
                        seen_tessie_dirs.add(real_d)
                        self.tessie_dirs.append(real_d)
            except Exception:
                pass

        # The ONE authoritative home for charges_master.csv and its
        # archive/ folder: config.json's own "tessie_directory" setting
        # (already resolved above as cfg_tessie_dir) if present, else the
        # default iCloud path, else (last resort - e.g. no config yet)
        # the repo's own Tessie folder. Deliberately singular - this used
        # to be written to EVERY discovered Tessie-like directory (repo
        # AND iCloud via self.tessie_dirs, which stays plural above for
        # registry/legacy-layout READING), which is why the repo ended up
        # holding its own drifting copy of real charging data, including
        # its own separate archive/ contents. User's explicit ask: nothing
        # should be archived (or duplicated at all) in the repo folder.
        if cfg_tessie_dir:
            # Trust the user's own configured location UNCONDITIONALLY -
            # do not silently fall back to the repo folder just because
            # this particular process can't currently see that path (e.g.
            # it doesn't exist yet, or a sandboxed/bridged shell resolves
            # a "~"-relative path differently than the user's real
            # machine does - see BUG-020's identical lesson). Only the
            # ABSENCE of a configured value falls through below.
            self.tessie_data_home = os.path.abspath(os.path.expanduser(cfg_tessie_dir))
        elif os.path.isdir(self.icloud_dir):
            self.tessie_data_home = os.path.abspath(os.path.realpath(self.icloud_dir))
        else:
            # Explicit user instruction: the repo folder is NEVER a valid
            # home for real master data, not even as a last-resort
            # fallback. If nothing is configured and the default iCloud
            # folder doesn't exist either, there is no data home -
            # consolidate_charges_master() below refuses to write rather
            # than silently defaulting into the repo.
            self.tessie_data_home = None

        if self.tessie_data_home:
            self.charges_dirs = [os.path.join(self.tessie_data_home, "charges")]
            try:
                os.makedirs(self.charges_dirs[0], exist_ok=True)
                os.makedirs(os.path.join(self.charges_dirs[0], "archive"), exist_ok=True)
            except Exception:
                pass
        else:
            self.charges_dirs = []

        # 3. Discover Invoices directories (CLI > config.json > local folders)
        primary_inv_dir = invoices_dir or self.config.get("invoices_directory") or self.config.get("invoices_dir")
        if primary_inv_dir:
            primary_inv_dir = os.path.expanduser(primary_inv_dir)

        self.invoice_dirs = []
        if primary_inv_dir and os.path.isdir(primary_inv_dir):
            self.invoice_dirs.append(os.path.abspath(os.path.realpath(primary_inv_dir)))
        else:
            candidates_invoices = [
                os.path.expanduser("~/iCloud/PDF/Tesla/charging_invoices"),
                os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/charging_invoices"),
                os.path.expanduser("~/iCloud/PDF/Tesla/Supercharging"),
                os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/Supercharging"),
                os.path.expanduser("~/iCloud/PDF/Tesla"),
                os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla"),
                os.path.join(self.repo_root, "Tessie", "charging_invoices"),
                os.path.join(self.repo_root, "Tessie", "invoices"),
                os.path.join(self.icloud_dir, "charging_invoices"),
                os.path.join(self.icloud_dir, "invoices"),
                os.path.expanduser("~/Documents/Tesla/Invoices"),
                os.path.expanduser("~/Downloads/Tesla Invoices"),
                os.path.expanduser("~/Downloads/Invoices")
            ]
            seen_inv_dirs = set()
            for d in candidates_invoices:
                try:
                    if d and os.path.isdir(d):
                        real_d = os.path.abspath(os.path.realpath(d))
                        if real_d not in seen_inv_dirs:
                            seen_inv_dirs.add(real_d)
                            self.invoice_dirs.append(real_d)
                except Exception:
                    pass

        # 4. Load Registries
        self.superchargers = self.load_json_registry("tesla_superchargers.json") or self.load_json_registry("superchargers.json")
        self.superchargers_archived = self.load_json_registry("tesla_superchargers_archived.json") or self.load_json_registry("superchargers_archived.json")
        self.personal_chargers = self.load_json_registry("personal_chargers.json")
        self.plugshare_chargers = self.load_json_registry("plugshare_chargers.json")
        self.plugshare_archived = self.load_json_registry("plugshare_chargers_archived.json")
        self.chargefox_chargers = self.load_json_registry("chargefox_chargers.json")
        self.chargefox_archived = self.load_json_registry("chargefox_chargers_archived.json")
        self.evie_chargers = self.load_json_registry("evie_chargers.json")
        self.evie_archived = self.load_json_registry("evie_chargers_archived.json")
        self.evx_chargers = self.load_json_registry("evx_chargers.json")
        self.evx_archived = self.load_json_registry("evx_chargers_archived.json")
        self.bppulse_chargers = self.load_json_registry("bppulse_chargers.json")
        self.bppulse_archived = self.load_json_registry("bppulse_chargers_archived.json")
        self.charging_stations = self.load_json_registry("tesla_chargers.json") or self.load_json_registry("charging.json") or self.load_json_registry("destination_chargers.json")
        self.charging_archived = self.load_json_registry("tesla_chargers_archived.json") or self.load_json_registry("charging_archived.json") or self.load_json_registry("destination_chargers_archived.json")
        self.consolidated_stations = self.load_json_registry("charging_stations.json")
        self.consolidated_archived = self.load_json_registry("charging_stations_archived.json")
        self.places = self.load_json_registry("places.json")

        # Invoice-number -> resolved location, written out beside the
        # invoice files (BUG-022): once we know WHICH invoice matched a
        # charge, the invoice's own printed station name is more
        # authoritative than anything guessed from Tessie's own (often
        # stale/manually-entered) saved_location or address text. This
        # cache also lets a human hand-correct one entry (add
        # "manual_override": true) and have it stick across future runs.
        self.invoice_location_map_path = (
            os.path.join(self.invoice_dirs[0], "invoice_locations.json") if self.invoice_dirs else None
        )
        self.invoice_location_map = {}
        if self.invoice_location_map_path and os.path.isfile(self.invoice_location_map_path):
            try:
                with open(self.invoice_location_map_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.invoice_location_map = loaded
            except Exception:
                pass
        self._invoice_location_map_dirty = False
        
        # 5. Detailed Telemetry CSVs
        self.vin = self.config.get("vin")
        self.landing_dir = os.path.expanduser(self.config.get("landing_directory", "~/Downloads"))
        self.detailed_charges = []

        self.charges = []
        self.invoices = []
        self.discrepancies = []
        self.reconciled_sessions = []
        self._loaded = False

    def load_config(self, explicit_config_path=None):
        config_candidates = [
            explicit_config_path,
            os.path.join(self.repo_root, "Tessie", "config.json"),
            os.path.join(self.repo_root, "config.json"),
            os.path.expanduser("~/.config/tesla/config.json"),
            os.path.join(self.icloud_dir, "config.json")
        ]
        for cp in config_candidates:
            if cp and os.path.isfile(cp):
                try:
                    with open(cp, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        if isinstance(cfg, dict):
                            self.config_file = os.path.abspath(cp)
                            return cfg
                except Exception:
                    pass
        return {}

    def load_json_registry(self, filename):
        """Loads one Tessie/<filename> hardware/station registry, tolerant
        of two on-disk shapes: the long-standing flat {name: {...station
        dict...}} convention every consumer here expects, and a raw
        scraper-output wrapper like {"version": ..., "updated_at": ...,
        "station_count": N, "stations": [...]} - some provider scrapers
        (e.g. find_chargers.py's Chargefox output, seen 2026-09-14) write
        THIS shape directly into chargefox_chargers.json instead of the
        normalized dict, with "stations" as a LIST of raw API objects, not
        a dict. The pre-existing unwrap below only handled a dict-shaped
        "stations" (the older "multi-view consolidated file" case); a
        list-shaped one fell through untouched, so "version"/"updated_at"/
        "station_count" got copied straight into the registry as if they
        were station entries - each a bare str/int, not a dict - and every
        later consumer (_resolve_location_candidates() etc, which assumes
        every registry value has a real hardware dict's .get()) crashed
        with "'str' object has no attribute 'get'" on the very first
        resolve_location() call. Confirmed live 2026-09-14: this took down
        charge/invoice reconciliation entirely (--unreconciled) the moment
        a Chargefox re-scrape landed mid-session.

        A list is now converted to a dict keyed by each entry's own
        "name"/"id" (falling back to a positional key, de-duped), and any
        entry that still isn't a dict after that - stray metadata scalars,
        chiefly - is skipped rather than stored, so a future schema
        surprise degrades (that one provider's stations are silently
        thinner/missing) instead of crashing every registry consumer.
        Note: a raw provider object's OWN fields (e.g. Chargefox's "lat"/
        "lng", no "keywords", no "network") don't match the normalized
        schema _candidate_geo()/_candidate_keywords()/_candidate_result()
        expect either - full support needs that provider's own normalizer
        (see Tools/providers/), not just this loader. This only guarantees
        the loader itself never crashes the rest of the pipeline."""
        data = {}
        for td in self.tessie_dirs:
            p = os.path.join(td, filename)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            # Handle multi-view consolidated file schema where stations dictionary is wrapped in 'stations'
                            if "stations" in loaded and isinstance(loaded["stations"], dict):
                                loaded = loaded["stations"]
                            elif "stations" in loaded and isinstance(loaded["stations"], list):
                                converted = {}
                                for i, item in enumerate(loaded["stations"]):
                                    if not isinstance(item, dict):
                                        continue
                                    key = str(item.get("name") or item.get("id") or f"station_{i}")
                                    if key in converted:
                                        key = f"{key}_{i}"
                                    converted[key] = item
                                loaded = converted
                            for k, v in loaded.items():
                                # An *_archived.json entry legitimately
                                # stores a LIST of historical snapshots once
                                # a station has been re-verified with real
                                # changes more than once (get_expected_
                                # tariff_rate() already normalizes this via
                                # "if isinstance(candidates, dict):
                                # candidates = [candidates]" a few hundred
                                # lines down) - this filter used to require
                                # isinstance(v, dict) for EVERY value, which
                                # silently dropped every list-shaped archive
                                # entry entirely. Confirmed live 2026-09-14
                                # (BUG-065, AGENTS.local.md): all 8 keys in
                                # tesla_superchargers_archived.json are now
                                # lists (multiple re-verifications this
                                # session), so self.superchargers_archived
                                # loaded EMPTY - silently breaking every
                                # archived-tariff lookup in
                                # get_expected_tariff_rate() and the new
                                # resolve_effective_registry_entry(), not
                                # just the invoice renamer that surfaced it.
                                if isinstance(v, dict):
                                    if k not in data:
                                        data[k] = v
                                elif isinstance(v, list):
                                    valid_items = [item for item in v if isinstance(item, dict)]
                                    if valid_items and k not in data:
                                        data[k] = valid_items
                except Exception:
                    pass
        return data

    def _resolve_location_candidates(self):
        """One flat list of every registry entry this session knows about,
        each as (kind, name, data) - built once and cached, so
        resolve_location() can rank matches by GLOBAL priority (best signal
        wins across every registry) instead of "whichever entry the loop
        happened to reach first" (BUG-021 - see resolve_location's own
        docstring below for what that used to get wrong)."""
        if getattr(self, "_loc_candidates", None) is not None:
            return self._loc_candidates
        candidates = []
        for sc_name, sc_data in self.superchargers.items():
            candidates.append(("supercharger", sc_name, sc_data))
        for reg, source_type in [(self.personal_chargers, "personal"),
                                  (self.charging_stations, "destination"),
                                  (self.chargefox_chargers, "chargefox"),
                                  (self.evie_chargers, "evie"),
                                  (self.evx_chargers, "evx"),
                                  (self.bppulse_chargers, "bppulse"),
                                  (self.plugshare_chargers, "plugshare"),
                                  (self.consolidated_stations, "consolidated")]:
            if reg:
                for st_name, st_data in reg.items():
                    candidates.append((source_type, st_name, st_data))
        for p_name, p_data in self.places.items():
            candidates.append(("place", p_name, p_data))
        self._loc_candidates = candidates

        # Keyword usefulness for the last-resort keyword tier: a keyword is
        # only trustworthy for a substring match if it's specific enough to
        # actually identify ONE station. "Australia" or a bare brand name
        # like "Bunnings" appears on many unrelated entries' own keyword
        # lists (scraped verbatim from PlugShare place names/addresses) and
        # used to let a random early match steal a session that a much
        # more specific, later entry (or plain GPS distance) would have
        # gotten right - see BUG-021.
        #
        # Uniqueness is counted by DISTINCT NORMALIZED DISPLAY NAME, not by
        # raw registry-entry count (BUG-022 follow-up): the PlugShare scrape
        # sometimes stores the SAME real station twice under two different
        # dict keys (e.g. "Bunnings Gladesville" and "Bunnings Gladesville
        # (PlugShare #1517863)" - a re-scrape that only differs by its
        # trailing "(PlugShare #...)" suffix). A raw count of 2 wrongly
        # disqualified "Bunnings Gladesville" as if it were ambiguous, even
        # though both rows resolve to the exact same place - which sent a
        # real Bunnings Gladesville invoice to the generic "Gladesville"
        # fallback instead of matching it. Collapsing duplicate rows down to
        # one normalized identity before counting fixes that while still
        # correctly excluding genuinely shared words ("Bunnings" alone spans
        # 6 different stores; "Gladesville" alone spans 3).
        from collections import defaultdict
        kw_display_names = defaultdict(set)
        for kind, name, data in candidates:
            display_key = self._normalized_display_name(kind, name, data)
            for kw in self._candidate_keywords(kind, data):
                kw_display_names[kw.lower()].add(display_key)
        self._loc_kw_counts = {kw: len(names) for kw, names in kw_display_names.items()}
        return candidates

    @staticmethod
    def _normalized_display_name(kind, name, data):
        """Collapses registry-key variants of the SAME physical place (see
        the keyword-uniqueness comment above in _resolve_location_candidates)
        down to one identity: takes the same display name resolve_location()
        would show the user, then strips a trailing "(...)" parenthetical -
        the shape PlugShare re-scrape duplicates differ by - and lowercases."""
        if kind == "supercharger":
            display = name
        elif kind == "place":
            display = data.get("nickname") or name
        else:
            display = data.get("name") or name
        display = re.sub(r'\s*\([^)]*\)\s*$', '', str(display)).strip().lower()
        return display

    @staticmethod
    def _candidate_keywords(kind, data):
        if kind == "supercharger":
            return data.get("tesla_metadata", {}).get("keywords") or []
        if kind == "place":
            return data.get("keywords") or []
        return (data.get("keywords") or data.get("plugshare_metadata", {}).get("keywords")
                or data.get("tesla_metadata", {}).get("keywords") or [])

    @staticmethod
    def _candidate_geo(kind, data):
        """(lat, lon, radius_m) for one candidate, or (None, None, None)."""
        if kind == "supercharger":
            loc = data.get("location", {})
            return loc.get("lat"), loc.get("lon"), loc.get("radius_m", 250)
        if kind == "place":
            return data.get("lat"), data.get("lon"), data.get("radius_m", 150)
        loc = data.get("location", {})
        return (data.get("lat") or loc.get("lat"),
                data.get("lon") or loc.get("lon"),
                data.get("radius_m") or loc.get("radius_m", 150))

    def _candidate_result(self, kind, name, data, is_supercharger=False, is_fast=False):
        """The (display_name, network, emoji, data) tuple for one matched
        candidate - unchanged from the original per-registry logic, just
        factored out so every match tier (GPS/exact-name/keyword) below can
        share it."""
        if kind == "supercharger":
            return (name, "Tesla Supercharger", "🔴", data)
        if kind == "place":
            nickname = data.get("nickname") or name
            emoji = "🔴" if is_supercharger else ("🔌" if is_fast else "🅿️")
            net = "Tesla Supercharger" if is_supercharger else ("DC Fast" if is_fast else "AC Public")
            return (nickname, net, emoji, data)
        # personal / plugshare / destination / consolidated
        st_type = data.get("type", "ac")
        network = (data.get("network") or data.get("operator")
                   or data.get("plugshare_metadata", {}).get("network")
                   or ("Evnex" if st_type == "home" else "3rd-Party"))
        short_name = data.get("name") or name
        is_dest = "destination" in str(short_name).lower() or "destination" in str(network).lower() or kind == "destination"
        if st_type == "home" or kind == "personal":
            emoji = "🏠"
        elif is_dest:
            emoji = "🅿️"
        else:
            emoji = "⚡" if (is_fast or st_type == "dc_fast" or "supercharger" in str(network).lower()) else "🔌"
        return (short_name, network, emoji, data)

    def resolve_location(self, address, saved_loc="", lat=None, lon=None, is_supercharger=False, is_fast=False):
        """Matches a charge/drive event to a known place across every
        registry (Superchargers, personal, PlugShare, Tesla destination
        chargers, and general places), by priority:

        1. GPS radius - the single most reliable signal when we have real
           coordinates, so it's checked GLOBALLY (nearest match across
           every registry) before anything else, exactly like BUG-016's
           "GPS wins" fix in the drives analyzer.
        2. Exact saved-location name match, case-insensitive, also global.
        3. Keyword substring match - last resort only, and restricted to
           keywords that are actually specific to one station (BUG-021:
           this used to also allow bare, widely-shared words like
           "Australia" or "Bunnings" that matched dozens of unrelated
           entries, silently attributing a session to the wrong charger
           whenever a generic word happened to overlap and that entry was
           reached before the real one).

        Before this fix, all of the above ran per-entry with an immediate
        return the moment ANY tier matched for THAT entry - so a bad
        keyword hit on an early, unrelated entry could win outright over a
        correct exact-GPS match sitting later in the same registry.
        """
        addr_clean = (address or "").lower()
        saved_clean = (saved_loc or "").strip()
        candidates = self._resolve_location_candidates()

        # 1. GPS radius - nearest match wins, but a purpose-built charging
        #    registry entry (supercharger/personal/plugshare/destination -
        #    these carry a real network name/keywords) is preferred over a
        #    same-location entry that only exists in the generic "places"
        #    registry (added for drive-matching, not charging - no network
        #    name of its own), even if "places" happens to be a few metres
        #    closer. Only falls back to "places" when nothing more specific
        #    is within radius at all.
        if lat is not None and lon is not None:
            best_specific, best_specific_dist = None, None
            best_place, best_place_dist = None, None
            for kind, name, data in candidates:
                elat, elon, erad = self._candidate_geo(kind, data)
                if elat is None or elon is None:
                    continue
                dist = haversine_distance_m(lat, lon, elat, elon)
                if dist > erad:
                    continue
                if kind == "place":
                    if best_place_dist is None or dist < best_place_dist:
                        best_place, best_place_dist = (kind, name, data), dist
                else:
                    if best_specific_dist is None or dist < best_specific_dist:
                        best_specific, best_specific_dist = (kind, name, data), dist
            best = best_specific or best_place
            if best:
                kind, name, data = best
                return self._candidate_result(kind, name, data, is_supercharger, is_fast)

        # 2. Exact saved-location name match (case-insensitive), global.
        if saved_clean:
            saved_lower = saved_clean.lower()
            for kind, name, data in candidates:
                display = data.get("name") or data.get("nickname") or name
                if saved_lower == name.lower() or saved_lower == str(display).lower():
                    return self._candidate_result(kind, name, data, is_supercharger, is_fast)

        # 3. Keyword match - only keywords unique to ONE entry, long enough
        #    to mean something, and matched on WORD BOUNDARIES - a plain
        #    substring check let a short keyword like "Ville" (from "The
        #    Ville Resort Casino") match merely because it's a fragment
        #    inside an unrelated word ("gladesVILLE"), even though "Ville"
        #    itself was unique across the registry (BUG-022 follow-up,
        #    caught while re-testing this exact fix against a real
        #    Bunnings Gladesville invoice).
        MIN_KEYWORD_LEN = 5
        kw_counts = self._loc_kw_counts
        saved_lower_full = saved_clean.lower()
        for kind, name, data in candidates:
            for kw in self._candidate_keywords(kind, data):
                kwl = kw.lower()
                if len(kwl) < MIN_KEYWORD_LEN or kw_counts.get(kwl, 0) != 1:
                    continue
                pattern = r'\b' + re.escape(kwl) + r'\b'
                if (saved_lower_full and re.search(pattern, saved_lower_full)) or re.search(pattern, addr_clean):
                    return self._candidate_result(kind, name, data, is_supercharger, is_fast)

        display = saved_clean or (address.split(",")[0].strip() if address else "Unknown Location")
        if is_supercharger:
            return (display, "Tesla Supercharger", "🔴", {})
        elif is_fast:
            return (display, "3rd-Party Fast", "🔌", {})
        else:
            return (display, "AC Charger", "🅿️", {})

    def resolve_invoice_location(self, invoice):
        """The authoritative (place_name, network, emoji, registry_obj) for
        a MATCHED invoice (BUG-022). Once we know which invoice paid for a
        charge, the invoice's own printed station name/address is more
        trustworthy than anything guessed beforehand from Tessie's own
        saved_location (manually entered, sometimes stale/wrong - see
        BUG-016/BUG-021) or from GPS-free address text. Priority:

        1. A previously recorded entry in invoice_locations.json - always
           wins if it has "manual_override": true (a human fixed it once,
           it stays fixed); otherwise treated as a cached prior result.
        2. Fresh resolution via resolve_location(), using ONLY the
           invoice's own location text as the address/keyword-match input
           and deliberately NOT Tessie's saved_location - so a wrong
           saved_location can never override a correct invoice-derived
           match. GPS is passed through if the invoice itself carries
           coordinates (most don't; text-based matching is what wins the
           common case, e.g. an Exploren receipt's own "Location" line).

        Every fresh resolution is cached into invoice_location_map (queued
        for a single save_invoice_location_map() call at the end of
        reconcile()) so the mapping file beside the invoices stays a
        readable, hand-correctable record of what each invoice number
        actually paid for.
        """
        inv_num = invoice.get("invoice_number")
        loc_raw = invoice.get("location_raw") or ""
        cached = self.invoice_location_map.get(inv_num) if inv_num else None
        if cached and cached.get("manual_override"):
            return (cached.get("place_name") or loc_raw or "Unknown Station",
                    cached.get("network") or invoice.get("network", "3rd-Party"),
                    cached.get("emoji") or invoice.get("emoji", "🔌"),
                    {})

        is_super = invoice.get("network") == "Tesla Supercharger"
        place_name, network, emoji, reg_obj = self.resolve_location(
            loc_raw, saved_loc="", lat=invoice.get("latitude"), lon=invoice.get("longitude"),
            is_supercharger=is_super, is_fast=not is_super,
        )

        # The invoice's own printed network (parsed from its supplier/
        # network line, e.g. "Exploren") is the literal, correct identity
        # for a third-party charge - resolve_location() can only ever GUESS
        # a network, and when the GPS/keyword match lands on a plain
        # "places" registry entry (added originally for drive-matching, no
        # operator name of its own - see places.json), the best it can do
        # is a generic placeholder like "DC Fast" instead of the real
        # network the invoice names. Prefer the invoice's text whenever it
        # has one, so e.g. a Bunnings Rydalmere/Exploren invoice shows
        # "Exploren", not "DC Fast" (BUG-022 follow-up).
        if invoice.get("network"):
            network = invoice["network"]
        if invoice.get("emoji"):
            emoji = invoice["emoji"]

        if inv_num:
            entry = {
                "place_name": place_name,
                "network": network,
                "emoji": emoji,
                "location_raw": loc_raw,
                "last_verified": datetime.now().isoformat(timespec="seconds"),
            }
            if cached:
                entry["manual_override"] = cached.get("manual_override", False)
            if self.invoice_location_map.get(inv_num) != entry:
                self.invoice_location_map[inv_num] = entry
                self._invoice_location_map_dirty = True

        return (place_name, network, emoji, reg_obj)

    def save_invoice_location_map(self):
        """Persist invoice_location_map to invoice_locations.json beside
        the invoice files, if anything changed this run. Read-only when
        no invoice directory is configured or nothing changed."""
        if not self._invoice_location_map_dirty or not self.invoice_location_map_path:
            return
        try:
            os.makedirs(os.path.dirname(self.invoice_location_map_path), exist_ok=True)
            tmp = self.invoice_location_map_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.invoice_location_map, f, indent=2, sort_keys=True)
            os.replace(tmp, self.invoice_location_map_path)
            self._invoice_location_map_dirty = False
        except Exception:
            pass

    def resolve_effective_registry_entry(self, registry_obj, dt, place_name=None):
        """
        Returns (target_obj, is_archived) - the registry entry that was
        actually effective at datetime dt, consulting the archived
        registries' own valid_from/valid_to windows when they cover dt
        (falling back to the current registry_obj otherwise). Extracted
        from get_expected_tariff_rate()'s existing archived-lookup block on
        2026-09-14 (BUG-063, AGENTS.local.md) so callers other than tariff
        calculation - starting with rename_invoices() - can look up the
        name/short_name that was true at a given historic date instead of
        always using whatever the CURRENT registry entry says.

        rename_invoices() was using matched_session["registry_obj"]'s
        CURRENT short_name unconditionally regardless of the invoice's own
        date, so when West Gosford, NSW was renamed to Gosford, NSW on
        2026-09-13, it silently "fixed" two already-correctly-named historic
        invoices (2026-06-25, 2026-08-01 - both inside the archived West
        Gosford window) to the wrong, current name. Those two files were
        already renamed on disk before this fix landed and need a follow-up
        run of the (now-fixed) renamer to put them back.
        """
        target_obj = registry_obj
        is_archived = False
        if not dt:
            return target_obj, is_archived

        lookup_name = place_name or (registry_obj.get("tesla_metadata", {}).get("name") if registry_obj else None) or (registry_obj.get("name") if registry_obj else None)
        if not lookup_name:
            return target_obj, is_archived

        archived_sources = [
            self.superchargers_archived,
            self.charging_archived,
            self.consolidated_archived,
            self.chargefox_archived,
            self.evie_archived,
            self.evx_archived,
            self.plugshare_archived
        ]
        candidates = None
        for arch_dict in archived_sources:
            if not arch_dict or not isinstance(arch_dict, dict):
                continue
            if lookup_name in arch_dict:
                candidates = arch_dict[lookup_name]
                break
            for k, v in arch_dict.items():
                if k.lower() == lookup_name.lower() or k.lower() in lookup_name.lower() or lookup_name.lower() in k.lower():
                    candidates = v
                    break
            if candidates:
                break

        if candidates:
            if isinstance(candidates, dict):
                candidates = [candidates]
            for cand in candidates:
                v_from_str = cand.get("valid_from")
                v_to_str = cand.get("valid_to") or cand.get("archived_at")
                v_from_dt = parse_flexible_date(v_from_str) if v_from_str else None
                v_to_dt = parse_flexible_date(v_to_str) if v_to_str else None
                v_from = v_from_dt if v_from_dt is not None else datetime.min
                v_to = v_to_dt if v_to_dt is not None else datetime.max
                if v_from and v_from.tzinfo:
                    v_from = v_from.replace(tzinfo=None)
                if v_to and v_to.tzinfo:
                    v_to = v_to.replace(tzinfo=None)
                check_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                if v_from <= check_dt <= v_to:
                    target_obj = cand
                    is_archived = True
                    break

        return target_obj, is_archived

    def get_expected_tariff_rate(self, registry_obj, dt, place_name=None, is_non_tesla=False):
        """
        Determines the expected tariff rate ($/kWh), TOU schedule name, theoretical cost,
        and whether historical archived rates were used for a charging session at datetime dt.
        """
        if not dt:
            return {
                "rate_per_kwh": None,
                "schedule_name": None,
                "theoretical_cost": None,
                "theoretical_gst": None,
                "is_archived": False,
                "timezone": "UTC"
            }

        target_obj = registry_obj
        is_archived = False

        # Try to find historical archived version matching timestamp dt
        lookup_name = place_name or (registry_obj.get("tesla_metadata", {}).get("name") if registry_obj else None) or (registry_obj.get("name") if registry_obj else None)
        if lookup_name:
            archived_sources = [
                self.superchargers_archived,
                self.charging_archived,
                self.consolidated_archived,
                self.chargefox_archived,
                self.evie_archived,
                self.evx_archived,
                self.plugshare_archived
            ]
            candidates = None
            for arch_dict in archived_sources:
                if not arch_dict or not isinstance(arch_dict, dict):
                    continue
                if lookup_name in arch_dict:
                    candidates = arch_dict[lookup_name]
                    break
                for k, v in arch_dict.items():
                    if k.lower() == lookup_name.lower() or k.lower() in lookup_name.lower() or lookup_name.lower() in k.lower():
                        candidates = v
                        break
                if candidates:
                    break

            if candidates:
                if isinstance(candidates, dict):
                    candidates = [candidates]
                for cand in candidates:
                    v_from_str = cand.get("valid_from")
                    v_to_str = cand.get("valid_to") or cand.get("archived_at")
                    v_from_dt = parse_flexible_date(v_from_str) if v_from_str else None
                    v_to_dt = parse_flexible_date(v_to_str) if v_to_str else None
                    v_from = v_from_dt if v_from_dt is not None else datetime.min
                    v_to = v_to_dt if v_to_dt is not None else datetime.max
                    if v_from and v_from.tzinfo:
                        v_from = v_from.replace(tzinfo=None)
                    if v_to and v_to.tzinfo:
                        v_to = v_to.replace(tzinfo=None)
                    check_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                    if v_from <= check_dt <= v_to:
                        target_obj = cand
                        is_archived = True
                        break

        # Fallback registry lookup by place_name if target_obj is None
        if not target_obj and lookup_name:
            ln_lower = lookup_name.lower().strip()
            for sc_k, sc_v in self.superchargers.items():
                meta = sc_v.get("tesla_metadata", {})
                st_id = str(sc_v.get("id", "")).strip() or str(meta.get("findus_id", "")).strip()
                aliases = [str(a).lower() for a in sc_v.get("aliases", [])]
                addresses = [str(addr).lower() for addr in sc_v.get("addresses", [])]
                keywords = [str(kw).lower() for kw in meta.get("keywords", [])]
                if (sc_k.lower() == ln_lower or ln_lower in sc_k.lower() or sc_k.lower() in ln_lower or
                    ln_lower == st_id or
                    ln_lower in aliases or any(ln_lower in a or a in ln_lower for a in aliases) or
                    any(ln_lower in addr or addr in ln_lower for addr in addresses) or
                    any(ln_lower in kw or kw in ln_lower for kw in keywords)):
                    target_obj = sc_v
                    break
            if not target_obj:
                for ps_k, ps_v in self.plugshare_chargers.items():
                    ps_name = (ps_v.get("name") or ps_k).lower()
                    kws = [k.lower() for k in ps_v.get("keywords", []) or ps_v.get("plugshare_metadata", {}).get("keywords", [])]
                    if ps_k.lower() == ln_lower or ln_lower in ps_k.lower() or ps_name in ln_lower or ln_lower in ps_name or any(k in ln_lower for k in kws):
                        target_obj = ps_v
                        break
            if not target_obj:
                for pc_k, pc_v in self.personal_chargers.items():
                    if pc_k.lower() == ln_lower or ln_lower in pc_k.lower():
                        target_obj = pc_v
                        break
            if not target_obj:
                for cs_k, cs_v in self.charging_stations.items():
                    if cs_k.lower() == ln_lower or ln_lower in cs_k.lower():
                        target_obj = cs_v
                        break

        if not target_obj:
            return {
                "rate_per_kwh": None,
                "schedule_name": None,
                "theoretical_cost": None,
                "theoretical_gst": None,
                "is_archived": False,
                "timezone": "UTC"
            }

        loc = target_obj.get("location", {})
        tz_name = resolve_location_timezone(
            state=loc.get("state"),
            country=loc.get("country"),
            lat=loc.get("lat"),
            lon=loc.get("lon")
        )

        local_dt = dt
        tariffs = target_obj.get("tariffs") or {}
        cost_cfg = target_obj.get("tessie_cost_config") or target_obj.get("costs") or {}

        user_group = "non_tesla" if is_non_tesla else "tesla_members"
        if user_group in tariffs or "tesla_members" in tariffs:
            group_cfg = tariffs.get(user_group) or tariffs.get("tesla_members") or {}
            p_model = group_cfg.get("pricing_model", "time_of_use" if tariffs.get("has_tou_pricing") else "flat")
            schedules = group_cfg.get("rate_schedules", [])
            flat_rate = group_cfg.get("rate_per_kwh") if group_cfg.get("rate_per_kwh") is not None else group_cfg.get("per_kwh_flat")
            if flat_rate is None:
                flat_rate = cost_cfg.get("per_kwh_flat") if cost_cfg.get("per_kwh_flat") is not None else cost_cfg.get("flat_per_kwh")
        else:
            p_model = tariffs.get("pricing_model") or cost_cfg.get("pricing_model", "time_of_use" if tariffs.get("has_tou_pricing") else "flat")
            schedules = tariffs.get("rate_schedules") or cost_cfg.get("rate_schedules") or []
            candidates = [
                tariffs.get("per_kwh_flat"),
                tariffs.get("rate_per_kwh"),
                tariffs.get("flat_per_kwh"),
                tariffs.get("rate"),
                cost_cfg.get("per_kwh_flat"),
                cost_cfg.get("flat_per_kwh"),
                cost_cfg.get("rate_per_kwh"),
                cost_cfg.get("rate")
            ]
            flat_rate = next((float(c) for c in candidates if c is not None), None)

        if p_model == "flat" or not schedules:
            return {
                "rate_per_kwh": flat_rate,
                "schedule_name": "Flat Rate" if flat_rate is not None else None,
                "is_archived": is_archived,
                "timezone": tz_name
            }

        day_str = local_dt.strftime("%a")
        time_str = local_dt.strftime("%H:%M")
        month_str = local_dt.strftime("%b")

        matched_rate = None
        matched_sched_name = "TOU Schedule"

        for sched in schedules:
            days = sched.get("days", [])
            months = sched.get("months", [])
            s_time = sched.get("start_time", "00:00")
            e_time = sched.get("end_time", "24:00")
            rate = sched.get("rate_per_kwh")
            s_name = sched.get("name") or sched.get("label") or "TOU Rate"

            if days and day_str not in days:
                continue
            if months and month_str not in months:
                continue

            if s_time <= e_time:
                if s_time <= time_str < e_time:
                    matched_rate = rate
                    matched_sched_name = s_name
                    break
            else:
                if time_str >= s_time or time_str < e_time:
                    matched_rate = rate
                    matched_sched_name = s_name
        if matched_rate is None:
            matched_rate = flat_rate
            matched_sched_name = "Standard Rate" if flat_rate is not None else None

        return {
            "rate_per_kwh": matched_rate,
            "schedule_name": matched_sched_name,
            "is_archived": is_archived,
            "timezone": tz_name
        }

    def auto_ingest_from_landing(self):
        source_dirs = []
        if self.landing_dir and os.path.isdir(self.landing_dir):
            source_dirs.append((self.landing_dir, True))
        for td in self.tessie_dirs:
            if td and os.path.isdir(td):
                source_dirs.append((td, False))

        primary_charges_dir = self.charges_dirs[0] if self.charges_dirs else os.path.join(self.repo_root, "Tessie", "charges")
        archive_dir = os.path.join(primary_charges_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)

        moved = 0
        for s_dir, is_landing in source_dirs:
            try:
                files = os.listdir(s_dir)
            except Exception:
                continue

            for f in files:
                if not f.endswith(".csv") or f.startswith("."):
                    continue
                f_lower = f.lower()

                # If scanning Tessie root, ignore subdirectories, master files, drives, and system telemetry
                if not is_landing:
                    if f in ["charges_master.csv", "drives_master.csv"]:
                        continue
                    ignored_stems = ["drives", "telemetry_stream", "firmware", "tire", "battery_health", "idles"]
                    if any(ign in f_lower for ign in ignored_stems):
                        continue

                fp = os.path.join(s_dir, f)
                try:
                    with open(fp, "r", encoding="utf-8-sig") as csv_f:
                        reader = csv.reader(csv_f)
                        header = next(reader, None)
                        if not header:
                            continue
                        hset = set(h.strip() for h in header)

                        # Leave all drive files for tessie_drives_analyzer
                        if "Starting Location" in hset or (("Speed (km/h)" in hset or "Speed (mph)" in hset) and "Charger Power (kW)" not in hset):
                            continue

                        # Check if Charge Deep Dive
                        is_charge_deepdive = ("Charger Power (kW)" in hset or "Charger Voltage (V)" in hset or "Charging State" in hset) and "Starting Location" not in hset
                        if is_charge_deepdive:
                            try:
                                first_row = next(reader, None)
                                dt_str = None
                                if first_row and len(first_row) > 0:
                                    dt_str = first_row[0]
                                dt = parse_flexible_date(dt_str) if dt_str else None
                                # AGENTS.md TODO-033: fold the real AEST/AEDT
                                # abbreviation into the stamp, and remember
                                # this session's own UTC epoch so the copied
                                # file's mtime can be set to the real
                                # charge-start event below, not whenever this
                                # manual ingest happened to run.
                                dt_epoch = None
                                stamp = dt.strftime('%Y-%m-%d_%H%M') if dt else None
                                if dt:
                                    try:
                                        import tessie_timezone as _tztools
                                        abbr = _tztools.abbr_for_naive_local(dt)
                                        dt_epoch = _tztools.epoch_for_naive_local(dt)
                                        if abbr:
                                            stamp = f"{stamp}_{abbr}"
                                    except Exception:
                                        pass
                                if dt and self.charges_dirs:
                                    # Numbered against the first charges dir only, then
                                    # reused for every mirror dir below - so the same
                                    # logical file gets the same name everywhere instead
                                    # of risking a different "_NN" in each destination.
                                    deepdive_name = os.path.basename(unique_stamped_path(
                                        self.charges_dirs[0], "charge_deepdive",
                                        stamp, ext=".csv"))
                                elif dt:
                                    deepdive_name = f"charge_deepdive_{stamp}_00.csv"
                                else:
                                    deepdive_name = f"charge_deepdive_{f}"
                                for cd in self.charges_dirs:
                                    dest_fp = os.path.join(cd, deepdive_name)
                                    shutil.copy2(fp, dest_fp)
                                    if dt_epoch is not None:
                                        try:
                                            os.utime(dest_fp, (dt_epoch, dt_epoch))
                                        except Exception:
                                            pass
                                shutil.move(fp, next_archive_path(archive_dir, f))
                                print(f"\033[94m⚡ Saved Charge Deep Dive Telemetry (Kept Separate):\033[0m {deepdive_name}")
                                moved += 1
                            except Exception:
                                pass
                            continue

                        # Check if Charge Summary
                        is_charge_summary = ("Location" in hset and "Energy Added (kWh)" in hset)
                        if is_charge_summary:
                            dst = next_archive_path(archive_dir, f)
                            shutil.move(fp, dst)
                            print(f"\033[94m📥 Ingested & Archived to charges/archive:\033[0m {os.path.basename(dst)}")
                            moved += 1
                except Exception:
                    pass
        if moved > 0:
            print("")

    def patch_charge_record(self, s_at_target, loc_target, new_cost, new_rate):
        """
        Patches Cost and Cost Per kWh across charges_master.csv and all relevant Tessie charges CSVs,
        strictly preserving Energy Added (kWh) telemetry.
        """
        s_at_clean = (s_at_target or "").strip()
        loc_clean = (loc_target or "").strip()
        
        target_files = set()
        search_dirs = list(self.charges_dirs) + list(self.tessie_dirs)
        for td in search_dirs:
            if not os.path.isdir(td):
                continue
            try:
                fnames = os.listdir(td)
            except Exception:
                continue
            for fname in fnames:
                if (fname == "charges_master.csv" or "charges_summary" in fname or "-charges.csv" in fname) and not "telemetry" in fname:
                    target_files.add(os.path.join(td, fname))
                    
        patched_files_count = 0
        for fp in sorted(target_files):
            try:
                temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(fp), text=True)
                modified = False
                with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as out_f:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as in_f:
                        reader = csv.DictReader(in_f)
                        fieldnames = reader.fieldnames
                        writer = csv.DictWriter(out_f, fieldnames=fieldnames)
                        writer.writeheader()
                        
                        for row in reader:
                            started_col = next((col for col in row.keys() if col and col.startswith("Started At")), "Started At")
                            row_s_at = row.get(started_col, "").strip()
                            row_loc = row.get("Location", "").strip()
                            
                            # Match on started_at and location
                            if row_s_at == s_at_clean and (not loc_clean or row_loc == loc_clean or loc_clean in row_loc or row_loc in loc_clean):
                                if new_cost is not None and "Cost" in row:
                                    row["Cost"] = f"{float(new_cost):.2f}"
                                if new_rate is not None and "Cost Per kWh" in row:
                                    row["Cost Per kWh"] = f"{float(new_rate):.2f}"
                                modified = True
                                
                            writer.writerow(row)
                if modified:
                    os.replace(temp_path, fp)
                    patched_files_count += 1
                else:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
            except Exception:
                pass
                
        return patched_files_count

    def load_charges(self):
        self.auto_ingest_from_landing()
        raw_charges = []
        seen_keys = set()
        
        search_dirs = list(self.charges_dirs) + list(self.tessie_dirs)
        # A --tessie-dir override names the TESSIE ROOT (mirroring
        # config.json's own "tessie_directory"), so charges_master.csv
        # sitting under its "charges/" subfolder (post-TODO-023 layout)
        # needs the same nested check tessie_drives_analyzer.py's
        # load_drives() already does for "drives/drives_master.csv" -
        # without this, a --tessie-dir override could only ever find a
        # LEGACY flat-layout charges_master.csv, never today's real one.
        for td in list(self.tessie_dirs):
            nested = os.path.join(td, "charges")
            if os.path.isdir(nested) and nested not in search_dirs:
                search_dirs.append(nested)
        seen_dirs = set()
        for td in search_dirs:
            if not os.path.isdir(td):
                continue
            real_td = os.path.realpath(td)
            if real_td in seen_dirs:
                continue
            seen_dirs.add(real_td)
            try:
                fnames = sorted(os.listdir(td), key=lambda x: (x != "charges_master.csv", x))
            except Exception:
                continue
            arch_dir = os.path.join(td, "archive")
            if os.path.isdir(arch_dir):
                try:
                    for af in os.listdir(arch_dir):
                        if "charges" in af.lower() and "telemetry" not in af:
                            fnames.append(os.path.join("archive", af))
                except Exception:
                    pass
            for fname in fnames:
                if ("charges" in fname.lower()) and "telemetry" not in fname and not fname.endswith(".tmp"):
                    fpath = os.path.join(td, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                s_at = row.get("Started At (AEST)") or row.get("Started At")
                                if not s_at:
                                    continue
                                loc = row.get("Location", "")
                                added = row.get("Energy Added (kWh)", "")
                                key = (s_at.strip(), loc.strip(), added.strip())
                                if key in seen_keys:
                                    continue
                                seen_keys.add(key)
                                
                                s_dt = parse_flexible_date(s_at)
                                e_at_key = next((k for k in row.keys() if k and k.startswith("Ended At")), "Ended At")
                                e_at = row.get(e_at_key, "")
                                e_dt = parse_flexible_date(e_at) if e_at else None
                                
                                is_super = str(row.get("Supercharger", "")).strip().lower() == "true"
                                is_fast = str(row.get("Fast Charger", "")).strip().lower() == "true"
                                
                                try:
                                    lat = float(row.get("Latitude", 0)) if row.get("Latitude") else None
                                    lon = float(row.get("Longitude", 0)) if row.get("Longitude") else None
                                except Exception:
                                    lat, lon = None, None

                                try:
                                    dur = float(row.get("Duration (Minutes)", 0))
                                except Exception:
                                    dur = 0.0

                                try:
                                    kwh_added = float(row.get("Energy Added (kWh)", 0))
                                except Exception:
                                    kwh_added = 0.0

                                try:
                                    kwh_used = float(row.get("Energy Used (kWh)", 0))
                                except Exception:
                                    kwh_used = 0.0

                                try:
                                    cost = float(row.get("Cost", 0))
                                except Exception:
                                    cost = 0.0

                                try:
                                    cost_per_kwh = float(row.get("Cost Per kWh", 0))
                                except Exception:
                                    cost_per_kwh = 0.0

                                try:
                                    start_soc = int(float(row.get("Starting Battery (%)", 0)))
                                    end_soc = int(float(row.get("Ending Battery (%)", 0)))
                                except Exception:
                                    start_soc, end_soc = 0, 0

                                try:
                                    range_added = float(row.get("Rated Range Added (km)", 0))
                                except Exception:
                                    range_added = 0.0

                                try:
                                    odometer = float(row.get("Odometer (km)", 0))
                                except Exception:
                                    odometer = 0.0

                                place_name, network, emoji, reg_obj = self.resolve_location(
                                    loc, row.get("Saved Location", ""), lat, lon, is_super, is_fast
                                )

                                raw_charges.append({
                                    "started_at": s_dt,
                                    "started_at_str": s_at,
                                    "ended_at": e_dt,
                                    "ended_at_str": e_at,
                                    "duration_mins": dur,
                                    "location_raw": loc,
                                    "saved_location": row.get("Saved Location", ""),
                                    "place_name": place_name,
                                    "network": network,
                                    "emoji": emoji,
                                    "registry_obj": reg_obj,
                                    "latitude": lat,
                                    "longitude": lon,
                                    "is_supercharger": is_super,
                                    "is_fast_charger": is_fast,
                                    "energy_added_kwh": kwh_added,
                                    "energy_used_kwh": kwh_used,
                                    "cost": cost,
                                    "cost_per_kwh": cost_per_kwh,
                                    "start_soc": start_soc,
                                    "end_soc": end_soc,
                                    "range_added_km": range_added,
                                    "odometer_km": odometer,
                                    "source_file": fpath
                                })
                    except Exception:
                        pass
                        
        raw_charges.sort(key=lambda x: x["started_at"] or datetime.min)
        self.charges = raw_charges
        return self.charges

    def load_invoices(self):
        invoices = []
        seen_file_paths = set()
        for inv_dir in self.invoice_dirs:
            if not os.path.isdir(inv_dir):
                continue
            for root, _, files in os.walk(inv_dir):
                for f in sorted(files):
                    if f.startswith("."):
                        continue
                    ext = os.path.splitext(f)[1].lower()
                    if ext in [".pdf", ".csv", ".tsv", ".txt"]:
                        fpath = os.path.join(root, f)
                        real_fpath = os.path.realpath(fpath)
                        if real_fpath in seen_file_paths:
                            continue
                        seen_file_paths.add(real_fpath)
                        res = TeslaInvoiceParser.parse_invoice_file(fpath)
                        if isinstance(res, list):
                            for r in res:
                                if r and (r.get("date") or r.get("energy_kwh") or r.get("total_cost")):
                                    invoices.append(r)
                        elif isinstance(res, dict):
                            if res.get("date") or res.get("energy_kwh") or res.get("total_cost"):
                                invoices.append(res)
        
        # Deduplicate invoices
        seen_invoices = set()
        unique_invoices = []
        for inv in invoices:
            inv_key = (inv.get("invoice_number"), inv.get("date"), inv.get("energy_kwh"), inv.get("total_cost"))
            if inv_key in seen_invoices:
                continue
            seen_invoices.add(inv_key)
            unique_invoices.append(inv)

        self.invoices = unique_invoices
        return self.invoices

    def parse_detailed_charge_csv(self, filepath):
        """
        Parses high-frequency second-by-second telemetry CSV files exported from Tessie
        (e.g., ~/Downloads/<VIN>-YYYY-MM-DD...csv or charge_deepdive_*.csv).
        """
        try:
            with open(filepath, "r", encoding="utf-8-sig", errors="ignore") as f:
                reader = list(csv.DictReader(f))
                if not reader:
                    return None
                
                header = set(reader[0].keys())
                if not ("Charging State" in header or "Charger Power (kW)" in header):
                    return None

                timestamps = []
                powers = []
                temps_min = []
                temps_max = []
                has_heater = False
                # Raw per-sample series for plotting a real charging curve
                # (see AGENTS.md REQ-035) - everything above this point only
                # ever kept aggregates (peak/avg), which is fine for the
                # audit text but useless for a curve.
                raw_ts = []
                raw_power = []
                raw_soc = []
                
                rem_start = None
                rem_end = None
                soc_start = None
                soc_end = None
                outside_temp = None
                inside_temp = None

                for row in reader:
                    ts_str = row.get("Timestamp (AEST)") or row.get("Timestamp")
                    if ts_str:
                        dt = parse_flexible_date(ts_str)
                        if dt:
                            timestamps.append(dt)
                    
                    p_val = row.get("Charger Power (kW)")
                    if p_val:
                        try:
                            p_num = float(p_val)
                            if p_num > 0:
                                powers.append(p_num)
                        except Exception:
                            pass
                    
                    t_min = row.get("Min Battery Module Temp (°C)")
                    if t_min:
                        try:
                            temps_min.append(float(t_min))
                        except Exception:
                            pass
                    
                    t_max = row.get("Max Battery Module Temp (°C)")
                    if t_max:
                        try:
                            temps_max.append(float(t_max))
                        except Exception:
                            pass
                    
                    if str(row.get("Battery Heater", "")).strip() in ("1", "true", "True"):
                        has_heater = True
                    
                    rem_val = row.get("Energy Remaining (kWh)")
                    if rem_val:
                        try:
                            val_num = float(rem_val)
                            if rem_start is None:
                                rem_start = val_num
                            rem_end = val_num
                        except Exception:
                            pass

                    soc_val = row.get("Battery Level (%)")
                    if soc_val:
                        try:
                            soc_num = int(float(soc_val))
                            if soc_start is None:
                                soc_start = soc_num
                            soc_end = soc_num
                        except Exception:
                            pass
                    
                    raw_ts.append(ts_str)
                    try:
                        raw_power.append(float(p_val) if p_val not in (None, "") else None)
                    except Exception:
                        raw_power.append(None)
                    try:
                        raw_soc.append(float(soc_val) if soc_val not in (None, "") else None)
                    except Exception:
                        raw_soc.append(None)

                    if outside_temp is None and row.get("Outside Temp (°C)"):
                        try:
                            outside_temp = float(row.get("Outside Temp (°C)"))
                        except Exception:
                            pass
                    if inside_temp is None and row.get("Inside Temp (°C)"):
                        try:
                            inside_temp = float(row.get("Inside Temp (°C)"))
                        except Exception:
                            pass

                if not timestamps:
                    return None

                min_dt = min(timestamps)
                max_dt = max(timestamps)
                dur_mins = (max_dt - min_dt).total_seconds() / 60.0

                # Numerical integration for energy
                pack_energies_kwh = 0.0
                dispenser_energies_kwh = 0.0
                for i in range(len(reader) - 1):
                    r0 = reader[i]
                    r1 = reader[i+1]
                    ts0 = r0.get("Timestamp (AEST)") or r0.get("Timestamp")
                    ts1 = r1.get("Timestamp (AEST)") or r1.get("Timestamp")
                    dt0 = parse_flexible_date(ts0)
                    dt1 = parse_flexible_date(ts1)
                    if dt0 and dt1 and dt1 > dt0:
                        dt_hours = (dt1 - dt0).total_seconds() / 3600.0
                        try:
                            p0 = float(r0.get("Charger Power (kW)") or 0)
                            p1 = float(r1.get("Charger Power (kW)") or 0)
                            dispenser_energies_kwh += 0.5 * (p0 + p1) * dt_hours
                        except Exception:
                            pass
                        try:
                            i0 = float(r0.get("Pack Current (A)") or 0)
                            v0 = float(r0.get("Pack Voltage (V)") or 0)
                            i1 = float(r1.get("Pack Current (A)") or 0)
                            v1 = float(r1.get("Pack Voltage (V)") or 0)
                            pack_energies_kwh += 0.5 * ((i0 * v0 + i1 * v1) / 1000.0) * dt_hours
                        except Exception:
                            pass

                delta_rem = (rem_end - rem_start) if (rem_start is not None and rem_end is not None) else None
                peak_kw = max(powers) if powers else 0.0
                avg_kw = (sum(powers) / len(powers)) if powers else 0.0

                initial_batt_temp = temps_min[0] if temps_min else None
                final_batt_temp = temps_max[-1] if temps_max else None
                max_batt_temp = max(temps_max) if temps_max else None
                temp_rise = (final_batt_temp - initial_batt_temp) if (initial_batt_temp is not None and final_batt_temp is not None) else None

                return {
                    "source_file": os.path.basename(filepath),
                    "source_path": filepath,
                    "start_datetime": min_dt,
                    "end_datetime": max_dt,
                    "duration_mins": dur_mins,
                    "samples_count": len(reader),
                    "soc_start": soc_start,
                    "soc_end": soc_end,
                    "energy_remaining_start": rem_start,
                    "energy_remaining_end": rem_end,
                    "delta_energy_remaining_kwh": delta_rem,
                    "peak_power_kw": peak_kw,
                    "avg_power_kw": avg_kw,
                    "initial_batt_temp_c": initial_batt_temp,
                    "final_batt_temp_c": final_batt_temp,
                    "max_batt_temp_c": max_batt_temp,
                    "temp_rise_c": temp_rise,
                    "battery_heater": has_heater,
                    "outside_temp_c": outside_temp,
                    "inside_temp_c": inside_temp,
                    "integrated_dispenser_kwh": dispenser_energies_kwh,
                    "integrated_pack_kwh": pack_energies_kwh,
                    "raw_samples": {
                        "timestamp": raw_ts,
                        "charger_power_kw": raw_power,
                        "battery_level_pct": raw_soc,
                    },
                }
        except Exception:
            return None

    def load_detailed_charges(self):
        # Mirrors load_detailed_drives() (tessie_drives_analyzer.py) - both
        # charges_dirs and drives_dirs get an "archive" AND "deepdive"
        # subfolder search (AGENTS.md TODO-023: charges/ was missing the
        # deepdive/ leg, so charge_deepdive_*.csv files living there - e.g.
        # the DC Supercharger sessions moved out of the charges/ root -
        # would otherwise go missing from this scan).
        detailed = []
        seen_paths = set()
        search_dirs = [self.landing_dir] + list(self.charges_dirs) + list(self.tessie_dirs)
        for cd in list(self.charges_dirs):
            for sub in ["archive", "deepdive"]:
                sub_p = os.path.join(cd, sub)
                if os.path.isdir(sub_p) and sub_p not in search_dirs:
                    search_dirs.append(sub_p)
        for td in list(self.tessie_dirs):
            for sub in ["charges", "telemetry", "archive"]:
                sub_p = os.path.join(td, sub)
                if os.path.isdir(sub_p):
                    search_dirs.append(sub_p)
            # Same "--tessie-dir override needs the deepdive/archive
            # subfolder treatment too" gap found and fixed on the drives
            # side (tessie_drives_analyzer.py's load_detailed_drives()) -
            # without this, deep-dive telemetry sitting in
            # <td>/charges/deepdive wasn't visible to this search whenever
            # td came from --tessie-dir rather than tessie_data_home.
            for sub in ["charges/deepdive", "charges/archive"]:
                sub_p = os.path.join(td, sub)
                if os.path.isdir(sub_p) and sub_p not in search_dirs:
                    search_dirs.append(sub_p)

        candidates = []
        for s_dir in search_dirs:
            if not s_dir or not os.path.isdir(s_dir):
                continue
            try:
                for fname in os.listdir(s_dir):
                    if not fname.endswith(".csv") or fname.startswith("."):
                        continue
                    is_match = False
                    if self.vin and fname.startswith(self.vin):
                        is_match = True
                    elif fname.startswith("charge_deepdive_"):
                        is_match = True
                    elif re.match(r"^[A-HJ-NPR-Z0-9]{17}-\d{4}-\d{2}-\d{2}", fname):
                        is_match = True

                    if is_match:
                        fpath = os.path.join(s_dir, fname)
                        real_p = os.path.realpath(fpath)
                        if real_p not in seen_paths:
                            seen_paths.add(real_p)
                            candidates.append(real_p)
            except Exception:
                pass

        for cp in candidates:
            parsed = self.parse_detailed_charge_csv(cp)
            if parsed:
                detailed.append(parsed)

        detailed.sort(key=lambda x: x["start_datetime"] or datetime.min)
        self.detailed_charges = detailed
        return self.detailed_charges

    def match_detailed_charge(self, charge):
        """The detailed_charges entry (if any) whose telemetry window
        covers this charging session - factored out of reconcile()'s own
        former inline loop so a second caller (the deep-dive backfill,
        AGENTS.md TODO-029/TODO-031) doesn't need a drifting second copy
        of the same matching logic. Mirrors tessie_drives_analyzer.py's
        match_detailed_drive() in spirit (same optional, may-or-may-not-
        exist lookup pattern): a close start-time match (<=15min) OR the
        detailed record's own start falling inside the session's
        start/end window."""
        if not getattr(self, "detailed_charges", None):
            return None
        dt = charge.get("started_at")
        if not dt:
            return None
        for dt_rec in self.detailed_charges:
            if dt_rec.get("start_datetime"):
                time_diff = abs((dt_rec["start_datetime"] - dt).total_seconds())
                if time_diff <= 900:
                    return dt_rec
                if charge.get("ended_at") and (charge["started_at"] <= dt_rec["start_datetime"] <= charge["ended_at"]):
                    return dt_rec
        return None

    def _load_drive_totals_in_period(self, start_dt, end_dt):
        """Real per-drive totals from drives_master.csv within
        [start_dt, end_dt] (naive LOCAL datetimes, same convention
        charge['started_at']/['datetime'] already use here) - both the
        'Distance (km)' column AND the car's own reported
        'Total Energy Used (kWh)' figure for each drive, so
        print_summary's driving-efficiency figure is the same real
        per-drive consumption Tessie/the car itself report (BUG - an
        earlier version derived "efficiency" from charging energy ADDED
        over the period divided by km driven, which is really a
        charging-supply-vs-driving-demand ratio that only coincidentally
        matches real efficiency when the battery's start/end SoC happen
        to net to about the same level - the user caught this by
        comparing against Tessie's own real 15.4 kWh/100km and asked for
        pure driving efficiency, with charging losses reported as a
        separate, additive figure instead of folded in).

        This is a direct, read-only CSV read (mirrors the column names
        and localize() handling tessie_drives_analyzer.py's own
        load_drives() uses) rather than importing/instantiating
        TessieAnalyzer - charges stay a self-contained domain (AGENTS.md's
        sync/analysis split), this is just borrowing drives_master.csv's
        own totals the same way tessie_api_sync.py's bulk-backfill
        already borrows the analyzer classes for a narrow, documented
        match-only purpose.

        Returns None (not zeros) if drives_master.csv can't be found at
        all, so callers can tell "no drive data available" apart from
        "zero km/energy really were recorded in this window". A drive
        row missing/unparseable 'Total Energy Used (kWh)' still counts
        toward distance_km - it just doesn't contribute to
        energy_used_kwh, same tolerant-per-field handling
        estimate_usable_capacity_kwh() (car_efficiency_profile.py) uses."""
        master_file = None
        for td in self.tessie_dirs:
            for candidate in [
                os.path.join(td, "drives", "drives_master.csv"),
                os.path.join(td, "drives_master.csv"),
            ]:
                if os.path.isfile(candidate):
                    master_file = candidate
                    break
            if master_file:
                break
        if not master_file:
            return None

        try:
            import tessie_timezone as _tztools
        except Exception:
            _tztools = None

        total_km = 0.0
        total_kwh_used = 0.0
        found_any = False
        with open(master_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                try:
                    utc_epoch = int(r["Started At (UTC)"]) if r.get("Started At (UTC)") else None
                    if utc_epoch is not None and _tztools is not None:
                        tz_name = r.get("Started At TZ") or _tztools.DEFAULT_TIMEZONE
                        local_dt, _abbr, _off = _tztools.localize(utc_epoch, tz_name)
                        dt_start = local_dt.replace(tzinfo=None)
                    else:
                        start_str = r.get("Started At (AEST)") or r.get("Started At") or r.get("Started")
                        if not start_str:
                            continue
                        dt_start = datetime.strptime(start_str.strip()[:16], "%Y-%m-%d %H:%M")
                    dist_km = float(r.get("Distance (km)", 0) or 0)
                except (ValueError, TypeError, KeyError):
                    continue
                if not (start_dt <= dt_start <= end_dt):
                    continue
                total_km += dist_km
                found_any = True
                try:
                    total_kwh_used += float((r.get("Total Energy Used (kWh)") or "").strip())
                except (ValueError, TypeError):
                    pass
        return {"distance_km": total_km, "energy_used_kwh": total_kwh_used} if found_any else None

    CHARGE_FINALIZE_GRACE_SECONDS = 600  # mirrors tessie_drives_analyzer.py's DRIVE_FINALIZE_GRACE_SECONDS

    def _charge_is_in_progress(self, session):
        """Mirrors tessie_drives_analyzer.py's TessieAnalyzer._drive_is_in_progress()
        for the same reason: don't fetch /states for a session that might
        still be plugged in and charging - Tessie's own sync can take a
        few minutes to finalize a session's summary row after it actually
        ends, and fetching too early risks permanently caching a truncated
        window. True when there's no recorded end time at all, or the end
        time is within the grace window."""
        ended_at = session.get("ended_at")
        if not ended_at:
            return True
        return (datetime.now() - ended_at).total_seconds() < self.CHARGE_FINALIZE_GRACE_SECONDS

    _EVNEX_JSON_STAMP_RE = re.compile(r"^charge_deepdive_(\d{4}-\d{2}-\d{2}_\d{4}(?:_(?:AEST|AEDT))?)_\d{2}_Evnex_Home\.json$")

    def _matching_evnex_json_stamp(self, start_dt, end_dt):
        """Finds the existing charge_deepdive_..._Evnex_Home.json file
        whose own charging window overlaps [start_dt, end_dt] - the same
        window-overlap match get_energy_source_crosscheck() uses, via
        ev_session_cost.load_evnex_deepdive_sessions() - and returns that
        file's own "<date>_<HHMM>" stamp (the value unique_stamped_path()
        was originally given when evnex_api_sync.py wrote it) rather than
        a full path. fetch_charge_deepdive_from_api() re-runs that same
        stamp through unique_stamped_path() itself, letting its own "_NN"
        disambiguator claim the next free slot.

        This two-step design (find the stamp, then let
        unique_stamped_path() mint the actual name) matters because a
        single Evnex plug-in session is not always one Tessie charge
        record: solar diversion can cycle the car's charging current on
        and off repeatedly as export rises and falls (or as the house's
        own load eats into the export margin), and Tessie treats each
        current-to-zero gap as the end of one charge and the start of
        the next, so charges_master.csv can hold several records for
        what Evnex itself logged as a single continuous session (AGENTS.md
        REQ-050). Returning a ready-made full path here would collide
        every one of those sub-sessions on the exact same filename, or
        silently overwrite an earlier sub-session's telemetry; handing
        back just the stamp and letting unique_stamped_path() run its
        own NN-disambiguator lands them at .../<stamp>_00_Evnex_Home.csv,
        _01, _02, ... - all still sharing the SAME date_time prefix as
        their one parent JSON, so they keep sorting and reading together.
        Returns None if no matching JSON is found; the caller falls back
        to a fresh stamp of its own in that case."""
        try:
            import ev_session_cost as _evc
        except Exception:
            return None
        pad = timedelta(minutes=2)
        try:
            evnex_sessions = _evc.load_evnex_deepdive_sessions()
        except Exception:
            return None
        for es in evnex_sessions:
            es_start_epoch = _evc.iso_to_epoch(es.get("charging_started_utc"))
            if es_start_epoch is None:
                continue
            es_end_epoch = _evc.iso_to_epoch(es.get("charging_stopped_utc")) or es_start_epoch
            try:
                es_tz = ZoneInfo(es.get("timezone") or "Australia/Sydney")
            except Exception:
                es_tz = ZoneInfo("Australia/Sydney")
            es_start_dt = datetime.fromtimestamp(es_start_epoch, tz=timezone.utc).astimezone(es_tz).replace(tzinfo=None)
            es_end_dt = datetime.fromtimestamp(es_end_epoch, tz=timezone.utc).astimezone(es_tz).replace(tzinfo=None)
            if es_start_dt <= end_dt + pad and es_end_dt >= start_dt - pad:
                json_path = es.get("_file")
                if not json_path:
                    continue
                m = self._EVNEX_JSON_STAMP_RE.match(os.path.basename(json_path))
                if m:
                    return m.group(1)
        return None

    def is_invoice_only(self, session):
        """Returns True if the charging session is an invoice-only record
        (e.g., historical charge appended from an invoice that predates Tessie
        API logging, with duration 0 and odometer 0) having no independent
        vehicle telemetry in Tessie."""
        return session.get("duration_mins", 0) <= 0 and session.get("odometer_km", 0) <= 0

    def _deepdive_location_suffix(self, session):
        """Builds the "_<Network>_<ShortName>" filename suffix that keeps
        an API-backfilled deepdive CSV in the SAME naming convention every
        manually-ingested/legacy deepdive file already uses (e.g.
        "_Tesla_Macquarie_NSW", "_Exploren_Bunnings_Gladesville") -
        charge_curve_chart.py's historical pooling keys entirely off that
        slug (via tesla_superchargers.json's short_name, through
        match_hardware()), so a session with no location in its filename
        silently drops out of its own site's pooling. Reuses the
        network/registry_obj/place_name resolve_location() already
        attached to this session at load_charges() time - no fresh
        lookup needed. Returns "_Evnex_Home" for a home session (the
        caller never actually reaches here for one - see
        fetch_charge_deepdive_from_api() - but this stays correct in
        isolation), or "" only if there's truly no usable name at all
        (never fatal - the file just falls back to a bare stamp)."""
        if session.get("network") == "Evnex":
            return "_Evnex_Home"

        reg_obj = session.get("registry_obj")
        short_name = ""
        if isinstance(reg_obj, dict):
            short_name = (reg_obj.get("tesla_metadata", {}) or {}).get("short_name", "") or ""
        if not short_name:
            loc_source = session.get("place_name") or session.get("location_raw") or ""
            loc_source = re.sub(r"\s*\(.*?\)", "", loc_source).strip()
            short_name = re.sub(r"[^A-Za-z0-9]+", "_", loc_source).strip("_")

        net_raw = str(session.get("network") or "")
        net_lower = net_raw.lower()
        if "supercharger" in net_lower or "tesla" in net_lower:
            net_slug = "Tesla"
        elif net_raw:
            net_slug = re.sub(r"[^A-Za-z0-9]+", "_", net_raw).strip("_")
        else:
            net_slug = ""

        parts = [p for p in (net_slug, short_name) if p]
        return ("_" + "_".join(parts)) if parts else ""

    def fetch_charge_deepdive_from_api(self, session):
        """Mirrors tessie_drives_analyzer.py's TessieAnalyzer.fetch_drive_deepdive_from_api()
        almost exactly (AGENTS.md TODO-029's own "prerequisite" - this IS
        that prerequisite, built here) - auto-fetches ONE session's
        detailed telemetry from Tessie's own GET /{vin}/states endpoint
        when no local charge_deepdive_*.csv already covers it, using the
        same format="csv" convention parse_detailed_charge_csv() already
        expects. Covers every session Tessie itself sees regardless of
        network (Superchargers, 3rd-party DC, AC destination, and home
        Evnex) - not just Superchargers.

        Home Evnex sessions ARE included (AGENTS.md REQ-050 - an earlier
        version of this method deliberately skipped them on the theory
        that /states has nothing to add over evnex_api_sync.py's own
        JSON; live-verified against a real Evnex session's own recorded
        window that this was wrong - Tessie polls the car's own charge
        state during ANY plugged-in session, home included, and this is
        the only source that shows the CAR's real per-sample amperage/
        voltage during that session, which the Evnex JSON's session-level
        totals cannot: a flat, high, unwavering current is the user
        overriding the charger with a fixed max-amp setting in the Tesla
        app; a current that rides up and down with the sun is Evnex's own
        solar-diversion controller doing its job. Evnex's own JSON
        remains the richer source for anything Evnex itself measures
        (tariff split, carbon mix) that the car has no visibility into -
        this is additive, not a replacement.

        charges_master.csv's own started_at/ended_at are naive LOCAL
        datetimes with no stored UTC epoch (TODO-009's charges-side
        migration is still open - see AGENTS.md) - converted to a real
        UTC epoch here via the same fixed Australia/Sydney assumption
        already documented as valid for all data to date, rather than
        adding a second, competing timezone-handling path ahead of that
        migration landing properly.

        Writes into charges_dirs[0]/deepdive/ (TODO-023's canonical home).
        For a non-Evnex session, named via the SAME unique_stamped_path()-
        based scheme auto_ingest_from_landing() already uses for manually-
        exported charge deep-dives, so both paths produce indistinguishable
        filenames. For an Evnex session, named to match the EXISTING
        charge_deepdive_..._Evnex_Home.json file's own stem (found by the
        same charging-window-overlap match get_energy_source_crosscheck()
        already uses, via ev_session_cost.load_evnex_deepdive_sessions())
        rather than minting a fresh name - the whole point (AGENTS.md
        REQ-050) is that the .json and .csv for one real session sort and
        pair together by filename alone; a fresh unique_stamped_path() call
        would tend to land on the same "_00" by coincidence for a single
        session but silently mismatch it for any session that collided
        with a sibling at JSON-write time. Falls back to a fresh
        unique_stamped_path()-based name only if no matching JSON is
        found (shouldn't happen for a real Evnex session, but never fatal).
        Self-validates with parse_detailed_charge_csv() before keeping it -
        a response that doesn't parse as charge telemetry is deleted and
        this returns None, same fail-safe as the drives side. Never called
        for a session still in progress - see _charge_is_in_progress().
        Returns the written file path on success, else None."""
        start_dt = session.get("started_at")
        end_dt = session.get("ended_at")
        if not start_dt or not end_dt or not self.charges_dirs or self.is_invoice_only(session):
            return None

        try:
            from tessie_api_common import load_config, get_token, get_vin, api_get, DEFAULT_UNIT_PARAMS
        except Exception:
            return None

        try:
            import tessie_timezone as _tztools
            local_tz = ZoneInfo(_tztools.DEFAULT_TIMEZONE)
        except Exception:
            local_tz = ZoneInfo("Australia/Sydney")
        start_epoch = start_dt.replace(tzinfo=local_tz).timestamp()
        end_epoch = end_dt.replace(tzinfo=local_tz).timestamp()

        cfg = load_config()
        token = get_token(None, cfg)
        vin = get_vin(None, cfg)
        if not token or not vin:
            return None

        pad = 120  # same +/-2min padding match_detailed_drive()/reconcile()'s own detailed-telemetry match already allows
        params = dict(DEFAULT_UNIT_PARAMS)
        params.update({
            "from": int(start_epoch) - pad,
            "to": int(end_epoch) + pad,
            "interval": 1,
            "format": "csv",
        })

        print("   ℹ️  No local deep-dive file for this charge - fetching detailed telemetry from Tessie...")
        status, body = api_get("states", vin, token, params)
        if status != 200:
            print(f"   ⚠️  Tessie /states fetch failed (HTTP {status}) - continuing without deep-dive telemetry.")
            return None

        dest_dir = os.path.join(self.charges_dirs[0], "deepdive")
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception:
            return None

        dest_path = None
        if session.get("network") == "Evnex":
            matched_stamp = self._matching_evnex_json_stamp(start_dt, end_dt)
            if matched_stamp:
                dest_path = unique_stamped_path(dest_dir, "charge_deepdive", matched_stamp, suffix="_Evnex_Home", ext=".csv")
        if not dest_path:
            # BUG (found 2026-09-14): this used to be
            # `"_Evnex_Home" if session.get("network") == "Evnex" else ""`
            # - every non-Evnex session got NO location in its filename at
            # all (just the bare "_NN" disambiguator from
            # unique_stamped_path()), unlike every manually-ingested/legacy
            # deepdive CSV, which is always named "..._<Network>_<ShortName>"
            # (e.g. "Tesla_Macquarie_NSW"). charge_curve_chart.py's historical
            # pooling keys ENTIRELY off that slug (via tesla_superchargers
            # .json's short_name) - a session named "..._00.csv" can never
            # match its own site's history, so it silently got NO dashed
            # line at all. Confirmed live: the 2026-09-09 22:27 Macquarie
            # Supercharger session was the first non-Evnex charge ever
            # auto-backfilled via THIS code path (every earlier Supercharger
            # session already had a manually-exported CSV, which took the
            # auto_ingest_from_landing() path instead) - its deepdive landed
            # as "charge_deepdive_2026-09-09_2227_AEST_00.csv" with slug "00",
            # invisible to every other Macquarie session's pooling.
            # _deepdive_location_suffix() below reuses the same
            # network/registry_obj/place_name resolve_location() already
            # attached to this session at load_charges() time, so the
            # result matches the existing naming convention exactly.
            suffix = self._deepdive_location_suffix(session)
            # AGENTS.md TODO-033: fold the real AEST/AEDT abbreviation into
            # the stamp here too, matching evnex_api_sync.py's own writer -
            # only reached when no existing Evnex JSON stamp was found to
            # match against above (matched_stamp already carries its own
            # tz suffix once present, via _EVNEX_JSON_STAMP_RE).
            stamp = start_dt.strftime("%Y-%m-%d_%H%M")
            try:
                abbr = start_dt.replace(tzinfo=local_tz).tzname()
                if abbr:
                    stamp = f"{stamp}_{abbr}"
            except Exception:
                pass
            dest_path = unique_stamped_path(dest_dir, "charge_deepdive", stamp, suffix=suffix, ext=".csv")

        try:
            with open(dest_path, "wb") as f:
                f.write(body)
        except Exception:
            return None
        # AGENTS.md TODO-033: set this file's mtime to the real charge-start
        # event (start_epoch, already computed above), not whenever this
        # auto-fetch happened to run.
        try:
            os.utime(dest_path, (start_epoch, start_epoch))
        except Exception:
            pass

        parsed = self.parse_detailed_charge_csv(dest_path)
        if not parsed or not parsed.get("raw_samples", {}).get("timestamp"):
            try:
                os.remove(dest_path)
            except Exception:
                pass
            print("   ⚠️  Tessie's response doesn't look like charge telemetry - continuing without deep-dive.")
            return None

        print(f"   ✅ Fetched and saved: {os.path.basename(dest_path)} ({parsed.get('samples_count', 0)} samples)")
        return dest_path

    def match_location(self, inv_loc, inv_net, charge):
        if not inv_loc:
            if charge["is_supercharger"] and inv_net == "Tesla Supercharger":
                return True
            return True

        inv_toks = clean_tokens(inv_loc)
        ch_toks = clean_tokens(charge["location_raw"])
        place_toks = clean_tokens(charge["place_name"])
        saved_toks = clean_tokens(charge.get("saved_location", ""))
        all_ch_toks = ch_toks | place_toks | saved_toks

        meaningful_common = {t for t in inv_toks.intersection(all_ch_toks) if len(t) >= 3 and t not in ["street", "road", "avenue", "highway", "south", "wales", "nsw", "australia"]}
        if meaningful_common:
            return True

        inv_lower = inv_loc.lower()
        if inv_lower in charge["location_raw"].lower() or inv_lower in charge["place_name"].lower():
            return True

        if charge["is_supercharger"] and any(sc in inv_lower for sc in ["supercharg", "tesla"]):
            return True

        return False

    
    def interactive_discrepancy_menu(self):
        """Displays all discrepancies in a menu and allows updating the underlying CSVs."""
        if not self.discrepancies:
            return
            
        print(f"\n\033[91m==========================================================================\033[0m")
        print(f"\033[91m ⚠️  {len(self.discrepancies)} CHARGE COST DISCREPANCIES DETECTED (Net/Gross CSV issue)\033[0m")
        print(f"\033[91m==========================================================================\033[0m")
        
        for i, d in enumerate(self.discrepancies):
            s_at = d.get("started_at_str")
            place = d.get("place_name")
            bat_kwh = d.get("energy_added_kwh", 0)
            t_cost = d.get("cost", 0)
            
            disc = d.get("_discrepancy", {})
            i_cost = disc.get("invoice_cost", 0)
            i_rate = disc.get("invoice_rate", 0)
            i_kwh = disc.get("invoice_kwh", 0)
            
            print(f"\n\033[91m[{i+1}] {s_at} @ {place}\033[0m")
            print(f"    Tessie Telemetry: {bat_kwh:.2f} kWh | Cost: ${t_cost:.2f}")
            print(f"    Official Invoice: {i_kwh:.2f} kWh | Cost: ${i_cost:.2f} | Rate: ${i_rate:.2f}/kWh" if i_rate == round(i_rate, 2) else f"    Official Invoice: {i_kwh:.2f} kWh | Cost: ${i_cost:.2f} | Rate: ${i_rate:.2f}/kWh")
            
        print(f"\nOptions: [1-{len(self.discrepancies)}] to fix individually, [a]ll to fix all, [s]kip/continue")
        
        while True:
            try:
                choice = input("Select action: ").strip().lower()
                if choice in ['s', 'skip', 'c', 'continue', 'q', 'quit']:
                    break
                
                to_fix = []
                if choice in ['a', 'all']:
                    to_fix = self.discrepancies
                elif choice.isdigit() and 1 <= int(choice) <= len(self.discrepancies):
                    to_fix = [self.discrepancies[int(choice)-1]]
                else:
                    # Support comma separated
                    parts = choice.split(",")
                    valid = True
                    for p in parts:
                        p = p.strip()
                        if p.isdigit() and 1 <= int(p) <= len(self.discrepancies):
                            to_fix.append(self.discrepancies[int(p)-1])
                        elif p:
                            valid = False
                    if not valid or not to_fix:
                        print("Invalid selection.")
                        continue
                
                # Fix the chosen discrepancies
                import csv
                import tempfile
                
                # Group by source file
                by_file = {}
                for charge in to_fix:
                    sf = charge.get("source_file")
                    if sf:
                        by_file.setdefault(sf, []).append(charge)
                
                fixed_count = 0
                for sf, charges in by_file.items():
                    if not os.path.exists(sf):
                        continue
                        
                    # Create lookup map based on Started At and Location
                    lookup = {}
                    for c in charges:
                        s_at = c.get("started_at_str", "").strip()
                        loc = c.get("location_raw", "").strip()
                        added = f"{c.get('energy_added_kwh', 0):.2f}"
                        # Try to match uniquely
                        lookup[(s_at, loc)] = c
                    
                    temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(sf), text=True)
                    try:
                        with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as out_f:
                            with open(sf, "r", encoding="utf-8", errors="ignore") as in_f:
                                reader = csv.DictReader(in_f)
                                fieldnames = reader.fieldnames
                                writer = csv.DictWriter(out_f, fieldnames=fieldnames)
                                writer.writeheader()
                                
                                for row in reader:
                                    s_at = (row.get("Started At (AEST)") or row.get("Started At", "")).strip()
                                    loc = row.get("Location", "").strip()
                                    
                                    key = (s_at, loc)
                                    if key in lookup:
                                        c = lookup[key]
                                        disc = c.get("_discrepancy", {})
                                        i_cost = disc.get("invoice_cost")
                                        i_rate = disc.get("invoice_rate")
                                        
                                        if i_cost is not None and "Cost" in row:
                                            row["Cost"] = str(i_cost)
                                            c["cost"] = i_cost
                                        if i_rate is not None and "Cost Per kWh" in row:
                                            row["Cost Per kWh"] = str(i_rate)
                                            c["cost_per_kwh"] = i_rate
                                            
                                        c["status"] = "MATCHED ✅"
                                        
                                        # Remove from self.discrepancies so it doesn't show again
                                        if c in self.discrepancies:
                                            self.discrepancies.remove(c)
                                        fixed_count += 1
                                        
                                    writer.writerow(row)
                        
                        os.replace(temp_path, sf)
                    except Exception as e:
                        print(f"Error modifying {sf}: {e}")
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                
                print(f"✅ Fixed {fixed_count} discrepancies.")
                if not self.discrepancies:
                    break
                else:
                    # Reprint remaining
                    return self.interactive_discrepancy_menu()
                    
            except (KeyboardInterrupt, EOFError):
                break

    def reconcile(self, interactive=False):
        if not self._loaded:
            self.load_charges()
            self.load_invoices()
            self.load_detailed_charges()
            self._loaded = True

        self.discrepancies = []
        reconciled = []
        matched_invoice_indices = set()

        for c_idx, charge in enumerate(self.charges):
            dt = charge["started_at"]
            best_inv_idx = None
            best_inv = None
            min_time_diff = float("inf")

            for i_idx, inv in enumerate(self.invoices):
                if i_idx in matched_invoice_indices:
                    continue
                inv_dt = inv.get("date")
                if not inv_dt or not dt:
                    continue
                
                has_exact_time = not (inv_dt.hour == 0 and inv_dt.minute == 0 and inv_dt.second == 0)
                if has_exact_time:
                    time_diff = abs((dt - inv_dt).total_seconds()) / 60.0
                    if time_diff <= self.tolerance_mins:
                        if self.match_location(inv.get("location_raw"), inv.get("network"), charge):
                            if time_diff < min_time_diff:
                                min_time_diff = time_diff
                                best_inv_idx = i_idx
                                best_inv = inv
                else:
                    # Invoice has calendar date only
                    if dt.date() == inv_dt.date():
                        if self.match_location(inv.get("location_raw"), inv.get("network"), charge):
                            inv_cost = inv.get("total_cost")
                            if inv_cost is None or abs(charge["cost"] - inv_cost) < 0.50:
                                best_inv_idx = i_idx
                                best_inv = inv
                                break

            # Detailed telemetry match
            matched_detailed = self.match_detailed_charge(charge)

            tessie_bat_kwh = charge["energy_added_kwh"]
            tessie_car_kwh = charge["energy_used_kwh"]
            invoice_disp_kwh = None

            dispenser_kwh = tessie_car_kwh if tessie_car_kwh > 0 else tessie_bat_kwh
            battery_kwh = tessie_bat_kwh
            tessie_cost = charge["cost"]
            invoice_cost = None
            invoice_rate = None
            invoice_net_rate = None
            inv_num = None
            status = "HOME / AC 🏠" if charge["emoji"] == "🏠" else ("UNRECONCILED ❓" if (charge["is_supercharger"] or charge["is_fast_charger"]) else "AC UNBILLED 🅿️")

            # Defaults: the pre-invoice-match guess from Tessie's own
            # location/saved_location/GPS. Overridden below once we know
            # which invoice matched - the invoice's own printed station
            # name is more trustworthy than that guess (BUG-022).
            session_place_name = charge["place_name"]
            session_network = charge["network"]
            session_emoji = charge["emoji"]
            session_registry_obj = charge["registry_obj"]

            if best_inv:
                matched_invoice_indices.add(best_inv_idx)
                inv_num = best_inv.get("invoice_number")
                if best_inv.get("location_raw"):
                    session_place_name, session_network, session_emoji, inv_reg_obj = self.resolve_invoice_location(best_inv)
                    if inv_reg_obj:
                        session_registry_obj = inv_reg_obj
                if best_inv.get("energy_kwh"):
                    invoice_disp_kwh = best_inv["energy_kwh"]
                    dispenser_kwh = best_inv["energy_kwh"]
                if best_inv.get("total_cost") is not None:
                    invoice_cost = best_inv["total_cost"]
                
                # Gross rate vs Net ex-GST rate
                if best_inv.get("gross_rate") is not None:
                    invoice_rate = best_inv["gross_rate"]
                elif best_inv.get("unit_rate") is not None:
                    invoice_rate = best_inv["unit_rate"]
                elif invoice_cost is not None and invoice_disp_kwh and invoice_disp_kwh > 0:
                    invoice_rate = round(invoice_cost / invoice_disp_kwh, 2)
                    
                u_rate = best_inv.get("unit_rate")
                if u_rate is not None and invoice_rate is not None and abs(u_rate - invoice_rate) >= 0.01:
                    invoice_net_rate = u_rate

                cost_diff = abs((invoice_cost or 0) - tessie_cost) if invoice_cost is not None else 0.0
                t_rate = charge.get("cost_per_kwh") or 0.0
                rate_diff = abs((invoice_rate or 0) - t_rate) if (invoice_rate is not None and t_rate > 0) else 0.0

                if invoice_cost is not None and cost_diff >= 0.02:
                    status = "COST MISMATCH ⚠️"
                    charge["_discrepancy"] = {
                        "invoice_cost": invoice_cost,
                        "invoice_rate": invoice_rate,
                        "invoice_kwh": invoice_disp_kwh,
                        "cost_diff": cost_diff,
                        "rate_diff": rate_diff,
                        "status": status
                    }
                    self.discrepancies.append(charge)
                elif invoice_rate is not None and rate_diff >= 0.01:
                    status = "RATE MISMATCH ⚠️"
                    charge["_discrepancy"] = {
                        "invoice_cost": invoice_cost,
                        "invoice_rate": invoice_rate,
                        "invoice_kwh": invoice_disp_kwh,
                        "cost_diff": cost_diff,
                        "rate_diff": rate_diff,
                        "status": status
                    }
                    self.discrepancies.append(charge)
                else:
                    status = "MATCHED ✅"

            # An invoice-only entry (BUG-029): the session has no independent
            # Tessie telemetry at all - append_charge_to_master() wrote the
            # SAME single invoice kWh figure into both "Energy Added" and
            # "Energy Used" (there was never a separate car-intake vs.
            # battery-storage measurement), and duration/odometer/SoC are all
            # blank/zero because Tessie never actually recorded this charge
            # (e.g. an invoice from before Tessie API history began). Detected
            # by duration/odometer both being zero - a real charge always has
            # some nonzero duration and a nonzero odometer reading, so this
            # combination is otherwise unreachable. Never compute a "loss"
            # or "efficiency" from a single duplicated number.
            is_invoice_only_entry = charge["duration_mins"] <= 0 and charge["odometer_km"] <= 0

            if is_invoice_only_entry:
                cable_loss_kwh = None
                car_loss_kwh = None
                loss_kwh = None
                efficiency_pct = None
                car_overshoot_kwh = 0.0
            elif invoice_disp_kwh and invoice_disp_kwh > 0:
                car_inlet = tessie_car_kwh if tessie_car_kwh > 0 else battery_kwh
                # BUG-030: max(0.0, ...) here used to silently clamp a
                # negative "cable loss" (car reporting MORE intake than the
                # dispenser/invoice billed - physically backwards) to a
                # falsely clean 0.00 kWh, hiding a real measurement
                # disagreement rather than surfacing it. A >= 0.05 kWh
                # overshoot (outside normal meter rounding) is now flagged
                # explicitly instead of being absorbed into "0 loss".
                raw_cable_delta = invoice_disp_kwh - car_inlet
                cable_loss_kwh = max(0.0, raw_cable_delta)
                car_overshoot_kwh = max(0.0, -raw_cable_delta) if raw_cable_delta < -0.05 else 0.0
                car_loss_kwh = max(0.0, car_inlet - battery_kwh)
                loss_kwh = max(0.0, invoice_disp_kwh - battery_kwh)
                efficiency_pct = (battery_kwh / invoice_disp_kwh * 100.0)
            else:
                cable_loss_kwh = 0.0
                car_overshoot_kwh = 0.0
                car_loss_kwh = max(0.0, tessie_car_kwh - battery_kwh) if tessie_car_kwh > 0 else 0.0
                loss_kwh = car_loss_kwh
                efficiency_pct = (battery_kwh / dispenser_kwh * 100.0) if dispenser_kwh > 0 else 100.0
            
            exp_info = self.get_expected_tariff_rate(session_registry_obj, dt, place_name=session_place_name)
            expected_rate = exp_info.get("rate_per_kwh")
            expected_sched = exp_info.get("schedule_name")
            is_archived_match = exp_info.get("is_archived", False)
            tz_used = exp_info.get("timezone", "Australia/Sydney")

            effective_kwh = dispenser_kwh if dispenser_kwh > 0 else battery_kwh
            if expected_rate is not None and effective_kwh > 0:
                theoretical_cost = effective_kwh * expected_rate
                theoretical_gst = theoretical_cost / 11.0
            else:
                theoretical_cost = None
                theoretical_gst = None

            reconciled.append({
                "charge_index": None,
                "datetime": dt,
                "datetime_str": charge["started_at_str"],
                "duration_mins": charge["duration_mins"],
                "place_name": session_place_name,
                "network": session_network,
                "emoji": session_emoji,
                "is_supercharger": charge["is_supercharger"],
                "is_fast_charger": charge["is_fast_charger"],
                "start_soc": charge["start_soc"],
                "end_soc": charge["end_soc"],
                "range_added_km": charge["range_added_km"],
                "odometer_km": charge["odometer_km"],
                "tessie_bat_kwh": tessie_bat_kwh,
                "tessie_car_kwh": tessie_car_kwh,
                "invoice_disp_kwh": invoice_disp_kwh,
                "cable_loss_kwh": cable_loss_kwh,
                "car_overshoot_kwh": car_overshoot_kwh,
                "car_loss_kwh": car_loss_kwh,
                "total_loss_kwh": loss_kwh,
                "is_invoice_only_entry": is_invoice_only_entry,
                "dispenser_kwh": dispenser_kwh,
                "battery_kwh": battery_kwh,
                "loss_kwh": loss_kwh,
                "efficiency_pct": efficiency_pct,
                "tessie_cost": tessie_cost,
                "tessie_rate": charge["cost_per_kwh"],
                "invoice_cost": invoice_cost,
                "invoice_rate": invoice_rate,
                "invoice_net_rate": invoice_net_rate,
                "invoice_number": inv_num,
                "expected_rate": expected_rate,
                "expected_schedule_name": expected_sched,
                "theoretical_cost": theoretical_cost,
                "theoretical_gst": theoretical_gst,
                "is_archived_tariff": is_archived_match,
                "timezone": tz_used,
                "status": status,
                "matched_invoice": best_inv,
                "detailed_telemetry": matched_detailed,
                "raw_charge": charge
            })

        for i_idx, inv in enumerate(self.invoices):
            if i_idx not in matched_invoice_indices:
                inv_dt = inv.get("date")
                if inv.get("location_raw"):
                    unmatched_place_name, unmatched_network, unmatched_emoji, _ = self.resolve_invoice_location(inv)
                else:
                    unmatched_place_name, unmatched_network, unmatched_emoji = (
                        "Unknown Station", inv.get("network", "3rd-Party"), inv.get("emoji", "🔌"))
                exp_inv_info = self.get_expected_tariff_rate(None, inv_dt, place_name=inv.get("location_raw"))
                inv_expected_rate = exp_inv_info.get("rate_per_kwh")
                inv_expected_sched = exp_inv_info.get("schedule_name")
                inv_is_archived = exp_inv_info.get("is_archived", False)
                inv_tz = exp_inv_info.get("timezone", "Australia/Sydney")
                inv_kwh = inv.get("energy_kwh") or 0.0
                inv_th_cost = (inv_kwh * inv_expected_rate) if (inv_expected_rate is not None and inv_kwh > 0) else None
                inv_th_gst = (inv_th_cost / 11.0) if inv_th_cost is not None else None

                reconciled.append({
                    "charge_index": None,
                    "datetime": inv_dt,
                    "datetime_str": inv_dt.strftime("%Y-%m-%d %H:%M") if inv_dt else "Unknown Date",
                    "duration_mins": 0,
                    "place_name": unmatched_place_name,
                    "network": unmatched_network,
                    "emoji": unmatched_emoji,
                    "is_supercharger": (inv.get("network") == "Tesla Supercharger"),
                    "is_fast_charger": True,
                    "start_soc": 0,
                    "end_soc": 0,
                    "range_added_km": 0.0,
                    "odometer_km": 0.0,
                    "dispenser_kwh": inv.get("energy_kwh") or 0.0,
                    "invoice_disp_kwh": inv.get("energy_kwh") or 0.0,
                    "battery_kwh": 0.0,
                    "loss_kwh": 0.0,
                    "efficiency_pct": 0.0,
                    "tessie_cost": 0.0,
                    "tessie_rate": 0.0,
                    "invoice_cost": inv.get("total_cost"),
                    "invoice_rate": inv.get("unit_rate"),
                    "invoice_number": inv.get("invoice_number"),
                    "expected_rate": inv_expected_rate,
                    "expected_schedule_name": inv_expected_sched,
                    "theoretical_cost": inv_th_cost,
                    "theoretical_gst": inv_th_gst,
                    "is_archived_tariff": inv_is_archived,
                    "timezone": inv_tz,
                    "status": "INVOICE ONLY 📄",
                    "matched_invoice": inv,
                    "raw_charge": None
                })

        reconciled.sort(key=lambda x: x["datetime"] or datetime.min)
        for i, session in enumerate(reconciled):
            session["charge_index"] = i + 1
        self.reconciled_sessions = reconciled
        self.save_invoice_location_map()
        return self.reconciled_sessions

    def patch_tessie_csv_record(self, filepath, target_started_at, new_cost, new_rate):
        import csv
        if not filepath or not os.path.exists(filepath):
            return False
        rows = []
        updated = False
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                for row in reader:
                    s_at = row.get("Started At (AEST)") or row.get("Started At")
                    if s_at and s_at.strip() == target_started_at.strip():
                        row['Cost'] = f"{new_cost:.2f}"
                        if 'Cost Per kWh' in row and new_rate is not None:
                            row['Cost Per kWh'] = f"{new_rate:.2f}"
                        updated = True
                    rows.append(row)
            
            if updated:
                with open(filepath, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                return True
        except Exception as e:
            print(f"\033[31mFailed to patch CSV {filepath}: {e}\033[0m")
        return False

    def consolidate_charges_master(self, output_dir=None):
        dest_dirs = []
        if output_dir:
            dest_dirs.append(output_dir)
        else:
            dest_dirs = list(self.charges_dirs)

        if not dest_dirs:
            print("\033[31mNo Tessie data directory configured - set \"tessie_directory\" in Tessie/config.json "
                  "(see Tessie/config.example.json). Refusing to write charges_master.csv into the repo folder.\033[0m")
            return 0

        for d in dest_dirs:
            try:
                os.makedirs(d, exist_ok=True)
                os.makedirs(os.path.join(d, "archive"), exist_ok=True)
            except Exception:
                pass

        master_files = [os.path.join(d, "charges_master.csv") for d in dest_dirs]
        master_file = master_files[0]

        existing_records = []
        existing_keys = set()
        existing_disk_row_count = 0
        
        fieldnames = [
            "Started At", "Ended At", "Duration (Minutes)", "Location", "Saved Location",
            "Latitude", "Longitude", "Supercharger", "Fast Charger", "Odometer (km)",
            "Energy Added (kWh)", "Energy Used (kWh)", "Rated Range Added (km)", "Starting Battery (%)",
            "Ending Battery (%)", "Cost", "Cost Per kWh"
        ]
        
        import csv
        for mf in master_files:
            if os.path.exists(mf):
                try:
                    with open(mf, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        # Dynamically detect Started At column to handle variable timezones
                        s_at_key = next((k for k in reader.fieldnames if k and k.startswith("Started At")), "Started At")
                        e_at_key = next((k for k in reader.fieldnames if k and k.startswith("Ended At")), "Ended At")
                        
                        # Update fieldnames to match the existing file's timezone formatting if it has one
                        fieldnames[0] = s_at_key
                        fieldnames[1] = e_at_key
                        
                        for row in reader:
                            existing_disk_row_count += 1
                            s_at = row.get(s_at_key, "").strip()
                            loc = row.get("Location", "").strip()
                            added = row.get("Energy Added (kWh)", "").strip()
                            key = (s_at, loc, added)
                            if key not in existing_keys:
                                existing_keys.add(key)
                                existing_records.append(row)
                        break
                except Exception as e:
                    pass

        if not self.reconciled_sessions:
            self.reconcile()
            
        new_records = []
        for c in self.reconciled_sessions:
            if c["status"] == "INVOICE ONLY 📄":
                continue 
                
            s_at = str(c.get("datetime_str", "")).strip()
            loc = str(c.get("place_name") or c.get("location_raw", "")).strip()
            added = f"{c['tessie_bat_kwh']:.2f}"
            
            raw = c.get("raw_charge") or {}
            loc_key = str(raw.get("location_raw", loc)).strip()
            
            key = (s_at, loc_key, added)
            if key not in existing_keys:
                s_at_key = fieldnames[0]
                e_at_key = fieldnames[1]
                row = {
                    s_at_key: s_at,
                    e_at_key: raw.get("ended_at_str", ""),
                    "Duration (Minutes)": f"{c.get('duration_mins', 0):.0f}",
                    "Location": loc_key,
                    "Saved Location": raw.get("saved_location", ""),
                    "Latitude": f"{raw.get('latitude', 0):.6f}" if raw.get("latitude") is not None else "",
                    "Longitude": f"{raw.get('longitude', 0):.6f}" if raw.get("longitude") is not None else "",
                    "Supercharger": "true" if c.get("is_supercharger") else "false",
                    "Fast Charger": "true" if c.get("is_fast_charger") else "false",
                    "Odometer (km)": f"{c.get('odometer_km', 0):.2f}",
                    "Energy Added (kWh)": added,
                    "Energy Used (kWh)": f"{c.get('tessie_car_kwh', 0):.2f}",
                    "Rated Range Added (km)": f"{c.get('range_added_km', 0):.2f}",
                    "Starting Battery (%)": str(c.get("start_soc", "")),
                    "Ending Battery (%)": str(c.get("end_soc", "")),
                    "Cost": f"{c.get('tessie_cost', 0):.2f}",
                    "Cost Per kWh": f"{c.get('tessie_rate', 0):.2f}"
                }
                new_records.append(row)
                existing_keys.add(key)
                
        all_records = existing_records + new_records

        # Safety net (BUG-023/024/025 history), same as consolidate_drives():
        # back up the master file's pre-change state whenever the row count
        # is about to actually change - new charges landing, or stale
        # duplicates found on disk being cleaned up - before overwriting it.
        rows_changing = existing_disk_row_count != len(all_records)
        duplicates_found_in_existing = max(0, existing_disk_row_count - len(existing_records))
        if rows_changing:
            backup_dir = os.path.join(dest_dirs[0], "archive") if dest_dirs else None
            if backup_dir:
                os.makedirs(backup_dir, exist_ok=True)
                for mf in master_files:
                    if os.path.isfile(mf):
                        try:
                            shutil.copy2(mf, next_archive_path(backup_dir, os.path.basename(mf)))
                        except Exception:
                            pass

        for mf in master_files:
            try:
                with open(mf, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(all_records)
            except Exception:
                pass

        # Sanity-check what was actually written before reporting success.
        sanity_issues = []
        for mf in master_files:
            try:
                with open(mf, "r", encoding="utf-8-sig") as f:
                    written_lines = [ln for ln in f.read().splitlines() if ln.strip()]
                data_lines = written_lines[1:]
                if len(data_lines) != len(all_records):
                    sanity_issues.append(
                        f"{os.path.basename(mf)}: wrote {len(all_records)} rows but file now has {len(data_lines)} data lines")
                exact_dupes = len(data_lines) - len(set(data_lines))
                if exact_dupes > 0:
                    sanity_issues.append(
                        f"{os.path.basename(mf)}: {exact_dupes} exact-duplicate line(s) still present after dedup")
            except Exception as e:
                sanity_issues.append(f"{os.path.basename(mf)}: could not verify after write - {e}")
        if sanity_issues:
            print("\033[31m⚠ Consolidate sanity check failed:\033[0m")
            for issue in sanity_issues:
                print(f"   - {issue}")

        if duplicates_found_in_existing > 0:
            print(f"{C_ORANGE}🧹 Flagged & auto-removed {duplicates_found_in_existing} duplicate row(s) found in the existing charges_master.csv "
                  f"(pre-change backup saved to charges/archive/){C_RESET}")
        dest_names = ", ".join(shorten_display_path(m, 0) for m in master_files)
        print(f"\033[92mSuccessfully appended {len(new_records)} new charges (Total: {len(all_records)}) to:\033[0m {dest_names}")

    def print_summary(self, filtered_sessions=None):
        sessions = filtered_sessions if filtered_sessions is not None else self.reconciled_sessions
        if not sessions:
            print(f"{C_YELLOW}No charging sessions matching the selected criteria.{C_RESET}")
            return

        total_sessions = len(sessions)
        sc_sessions = [s for s in sessions if s["is_supercharger"]]
        fast_sessions = [s for s in sessions if s["is_fast_charger"] and not s["is_supercharger"]]
        home_sessions = [s for s in sessions if s["emoji"] == "🏠"]
        ac_dest_sessions = [s for s in sessions if not s["is_supercharger"] and not s["is_fast_charger"] and s["emoji"] != "🏠"]

        total_dispenser_kwh = sum(s["dispenser_kwh"] for s in sessions)
        total_battery_kwh = sum(s["battery_kwh"] for s in sessions)
        total_loss_kwh = max(0.0, total_dispenser_kwh - total_battery_kwh)
        overall_eff_pct = (total_battery_kwh / total_dispenser_kwh * 100.0) if total_dispenser_kwh > 0 else 100.0
        
        total_spend = sum((s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]) for s in sessions)
        avg_cost_kwh = (total_spend / total_dispenser_kwh) if total_dispenser_kwh > 0 else 0.0

        total_km_added = sum(s["range_added_km"] for s in sessions)
        petrol_equiv_spend = (total_km_added / 100.0) * 9.5 * 1.95 if total_km_added > 0 else (total_battery_kwh / 0.15 / 100.0) * 9.5 * 1.95
        savings = max(0.0, petrol_equiv_spend - total_spend)

        # Real km travelled (from drives_master.csv, not the rated-range
        # "added" figure above) over the exact same window these sessions
        # cover, plus the derived cost/efficiency/annualized figures that
        # only mean anything once anchored to real distance driven.
        #
        # Driving efficiency is deliberately taken from drives_master.csv's
        # OWN "Total Energy Used (kWh)" per drive (the same real per-drive
        # figure Tessie/the car report), NOT from how much energy was
        # ADDED via charging over the period - those are two different
        # things that only coincidentally match when the battery's
        # start/end SoC happen to net out to about the same level over the
        # window. Charging losses (dispenser->battery, already computed
        # above as total_loss_kwh) are reported as their own SEPARATE,
        # additive kWh/100km figure instead of being blended into "average
        # efficiency" - the user caught the earlier blended version
        # reading 19.3 kWh/100km against a real 15.4 kWh/100km from
        # Tessie/the car directly, and asked for pure driving efficiency
        # plus charging losses reported separately.
        PETROL_L_PER_100KM = 9.5  # same assumption already used for petrol_equiv_spend above
        PETROL_PRICE_PER_L = 1.95
        session_dts = [s["datetime"] for s in sessions if s.get("datetime")]
        km_travelled = None
        period_days = None
        driving_efficiency_kwh_per_100km = None
        charging_loss_kwh_per_100km = None
        cost_per_km = None
        projected_annual_km = None
        projected_annual_savings = None
        if session_dts:
            period_start = min(session_dts)
            period_end = max(session_dts)
            period_days = max((period_end - period_start).days, 1)
            drive_totals = self._load_drive_totals_in_period(period_start, period_end)
            km_travelled = drive_totals["distance_km"] if drive_totals else None
            if km_travelled and km_travelled > 0:
                real_kwh_used = drive_totals.get("energy_used_kwh") or 0.0
                driving_efficiency_kwh_per_100km = (real_kwh_used / km_travelled) * 100.0 if real_kwh_used > 0 else None
                charging_loss_kwh_per_100km = (total_loss_kwh / km_travelled) * 100.0
                cost_per_km = total_spend / km_travelled
                annualize_ratio = 365.25 / period_days
                projected_annual_km = km_travelled * annualize_ratio
                projected_annual_spend = total_spend * annualize_ratio
                projected_annual_petrol_equiv = (projected_annual_km / 100.0) * PETROL_L_PER_100KM * PETROL_PRICE_PER_L
                projected_annual_savings = max(0.0, projected_annual_petrol_equiv - projected_annual_spend)

        fully_matched_fast = sum(1 for s in sessions if (s["is_supercharger"] or s["is_fast_charger"]) and s["status"] == "MATCHED ✅")
        mismatched_fast = sum(1 for s in sessions if (s["is_supercharger"] or s["is_fast_charger"]) and "MISMATCH" in s["status"])
        total_fast_count = len(sc_sessions) + len(fast_sessions)

        box_w = 145
        print()
        print(f"┌{'─' * (box_w - 2)}┐")
        summary_title = f" ⚡ {C_BOLD}TESLA CHARGING & INVOICE RECONCILIATION SUMMARY{C_RESET}"
        print(f"│{pad_display(summary_title, box_w - 2, 'left')}│")
        print(f"├{'─' * (box_w - 2)}┤")
        
        kpi_l1 = f"  {C_BOLD}Total Charging Sessions:{C_RESET} {total_sessions} ({len(sc_sessions)} Supercharger, {len(fast_sessions)} DC Fast, {len(home_sessions)} Home AC, {len(ac_dest_sessions)} Dest AC)"
        print(f"│{pad_display(kpi_l1, box_w - 2)}│")
        
        kpi_l2 = f"  {C_BOLD}Energy Delivered (Meter):{C_RESET} {total_dispenser_kwh:,.2f} kWh  │  {C_BOLD}Energy Added (Battery):{C_RESET} {total_battery_kwh:,.2f} kWh"
        print(f"│{pad_display(kpi_l2, box_w - 2)}│")
        
        eff_color = C_GREEN if overall_eff_pct >= 85.0 else (C_YELLOW if overall_eff_pct >= 75.0 else C_RED)
        kpi_l3 = f"  {C_BOLD}Charging Efficiency Loss:{C_RESET} {total_loss_kwh:,.2f} kWh  ({eff_color}{overall_eff_pct:.1f}% Dispenser-to-Battery{C_RESET})"
        print(f"│{pad_display(kpi_l3, box_w - 2)}│")

        kpi_l4 = f"  {C_BOLD}Total Electricity Spend:{C_RESET} ${total_spend:,.2f} AUD  │  {C_BOLD}Average Cost:{C_RESET} ${avg_cost_kwh:.2f}/kWh"
        print(f"│{pad_display(kpi_l4, box_w - 2)}│")

        kpi_l5 = f"  {C_BOLD}Estimated Petrol Equivalent:{C_RESET} ${petrol_equiv_spend:,.2f}  │  {C_GREEN}{C_BOLD}Net Fuel Savings:{C_RESET} ${savings:,.2f} AUD"
        print(f"│{pad_display(kpi_l5, box_w - 2)}│")

        if mismatched_fast > 0:
            recon_details = f"{fully_matched_fast}/{total_fast_count} Matched, {mismatched_fast} Mismatched ⚠️"
            fast_status_color = C_YELLOW
        else:
            recon_details = f"{fully_matched_fast}/{total_fast_count} Fast Sessions Fully Matched ✅"
            fast_status_color = C_GREEN if (fully_matched_fast == total_fast_count and total_fast_count > 0) else C_YELLOW

        inv_src_info = f"({len(self.invoices)} Invoices Loaded)"
        kpi_l6 = f"  {C_BOLD}Invoice Reconciliation:{C_RESET} {fast_status_color}{recon_details}{C_RESET} {inv_src_info}"
        print(f"│{pad_display(kpi_l6, box_w - 2)}│")

        if km_travelled and km_travelled > 0:
            window_note = f"{period_days}-day window" if period_days and period_days < 300 else f"~{period_days}d"
            eff_disp = f"{driving_efficiency_kwh_per_100km:.1f} kWh/100km" if driving_efficiency_kwh_per_100km is not None else "n/a"
            kpi_l8 = (f"  {C_BOLD}Distance Travelled:{C_RESET} {km_travelled:,.1f} km ({window_note})  │  "
                      f"{C_BOLD}Driving Efficiency (real):{C_RESET} {eff_disp}")
            print(f"│{pad_display(kpi_l8, box_w - 2)}│")

            kpi_l8b = (f"  {C_BOLD}Charging Losses (overhead):{C_RESET} {total_loss_kwh:,.2f} kWh total  │  "
                       f"+{charging_loss_kwh_per_100km:.1f} kWh/100km")
            print(f"│{pad_display(kpi_l8b, box_w - 2)}│")

            kpi_l9 = (f"  {C_BOLD}Cost per km:{C_RESET} ${cost_per_km:.3f}/km  │  "
                      f"{C_BOLD}Projected Annual Distance:{C_RESET} {projected_annual_km:,.0f} km/yr")
            print(f"│{pad_display(kpi_l9, box_w - 2)}│")

            annualize_caveat = " (short window - rough)" if period_days and period_days < 30 else ""
            kpi_l10 = (f"  {C_GREEN}{C_BOLD}Projected Annual Savings vs Petrol:{C_RESET} "
                       f"${projected_annual_savings:,.2f} AUD/yr{annualize_caveat}")
            print(f"│{pad_display(kpi_l10, box_w - 2)}│")
        else:
            kpi_l8 = f"  {C_BOLD}Distance Travelled:{C_RESET} {C_ORANGE}No Tessie measurement for this session{C_RESET}"
            print(f"│{pad_display(kpi_l8, box_w - 2)}│")

        if self.config_file:
            cfg_disp = shorten_display_path(self.config_file, 55)
            inv_dir_val = self.config.get("invoices_directory") or (self.invoice_dirs[0] if self.invoice_dirs else "None")
            inv_disp = shorten_display_path(inv_dir_val, 55)
            kpi_l7 = f"  {C_BOLD}Config Loaded:{C_RESET} {cfg_disp} (Invoices: {inv_disp})"
            print(f"│{pad_display(kpi_l7, box_w - 2, truncate=True)}│")
        
        print(f"└{'─' * (box_w - 2)}┘\n")

        network_groups = defaultdict(list)
        for s in sessions:
            network_groups[s["network"]].append(s)

        headers = ["Network / Location", "Type", "Sessions", "Dispenser kWh", "Battery kWh", "Eff %", "Total Spend", "Avg $/kWh"]
        widths = [26, 10, 10, 15, 14, 9, 13, 11]
        net_inner_w = sum(widths) + len(widths) - 1

        print(f"┌{'─' * net_inner_w}┐")
        net_title = f" 📊 {C_BOLD}CHARGING NETWORK & LOCATION BREAKDOWN{C_RESET}"
        print(f"│{pad_display(net_title, net_inner_w, 'left', truncate=True)}│")
        top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
        print(top_b)
        
        h_row = "│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers, widths)) + "│"
        print(h_row)

        mid_b = "├" + "┼".join("─" * w for w in widths) + "┤"
        print(mid_b)

        for net_name, net_sessions in sorted(network_groups.items(), key=lambda x: len(x[1]), reverse=True):
            emoji = net_sessions[0]["emoji"]
            n_disp = sum(s["dispenser_kwh"] for s in net_sessions)
            n_bat = sum(s["battery_kwh"] for s in net_sessions)
            n_eff = (n_bat / n_disp * 100.0) if n_disp > 0 else 100.0
            n_cost = sum((s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]) for s in net_sessions)
            n_avg_rate = (n_cost / n_disp) if n_disp > 0 else 0.0
            
            type_label = "DC Fast" if net_sessions[0]["is_fast_charger"] else ("Home AC" if emoji == "🏠" else "AC Public")
            name_str = f"{emoji} {net_name}"

            row_str = "│" + "│".join([
                pad_display(f" {name_str}", widths[0], "left", truncate=True),
                pad_display(" " + type_label, widths[1], "left", truncate=True),
                pad_display(str(len(net_sessions)) + " ", widths[2], "right", truncate=True),
                pad_display(f"{n_disp:,.1f} kWh ", widths[3], "right", truncate=True),
                pad_display(f"{n_bat:,.1f} kWh ", widths[4], "right", truncate=True),
                pad_display(f"{n_eff:.1f}% ", widths[5], "right", truncate=True),
                pad_display(f"${n_cost:,.2f} ", widths[6], "right", truncate=True),
                pad_display(f"${n_avg_rate:.2f} ", widths[7], "right", truncate=True)
            ]) + "│"
            print(row_str)

        bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
        print(bot_b)
        print()

    def print_sessions_table(self, filtered_sessions=None):
        sessions = filtered_sessions if filtered_sessions is not None else self.reconciled_sessions
        if not sessions:
            return

        place_header_w = len("Place / Station")
        max_place_w = place_header_w
        max_inv_w = len("Invoice")
        for s in sessions:
            clean_place = s["place_name"]
            if s.get("network"):
                clean_place = re.sub(rf"\s*\({re.escape(s['network'])}\)", "", clean_place, flags=re.IGNORECASE)
            p_len = display_len(f" {s['emoji']} {clean_place}") + 1
            if p_len > max_place_w:
                max_place_w = p_len
            inv_num = str(s.get("invoice_number") or "")
            if len(inv_num) > max_inv_w:
                max_inv_w = len(inv_num)

        place_col_width = max(25, max_place_w)
        inv_col_width = max(20, max_inv_w + 2)

        headers = [
            "#", "Date / Time", "Place / Station", "Network", "SoC %", "Dur", "Disp kWh", "Bat kWh", "Eff %", "Rate", "Cost", "Invoice", "Status"
        ]
        widths = [
            4, 18, place_col_width, 22, 9, 6, 10, 9, 8, 9, 8, inv_col_width, 18
        ]
        total_inner_w = sum(widths) + len(widths) - 1

        print(f"┌{'─' * total_inner_w}┐")
        sess_title = f" ⚡ {C_BOLD}RECONCILED CHARGING SESSIONS ({len(sessions)} sessions){C_RESET}"
        print(f"│{pad_display(sess_title, total_inner_w, 'left', truncate=True)}│")
        top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
        print(top_b)
        
        h_row = "│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers, widths)) + "│"
        print(h_row)

        mid_b = "├" + "┼".join("─" * w for w in widths) + "┤"
        print(mid_b)

        for s in sessions:
            idx_str = str(s["charge_index"]) if s["charge_index"] is not None else "-"
            dt_str = s["datetime_str"][:16] if s["datetime_str"] else "-"
            
            clean_place = s["place_name"]
            if s.get("network"):
                clean_place = re.sub(rf"\s*\({re.escape(s['network'])}\)", "", clean_place, flags=re.IGNORECASE)
            place_str = f"{s['emoji']} {clean_place}".strip()

            net_str = s["network"]
            soc_str = f"{s['start_soc']}%➔{s['end_soc']}%" if (s["start_soc"] or s["end_soc"]) else "-"
            dur_str = f"{int(s['duration_mins'])}m" if s["duration_mins"] > 0 else "-"
            disp_str = f"{s['dispenser_kwh']:.2f}"
            bat_str = f"{s['battery_kwh']:.2f}"
            eff_str = f"{s['efficiency_pct']:.1f}%" if s.get("efficiency_pct") is not None else "N/A"
            
            if s.get("status") == "INVOICE ONLY 📄":
                rate_val = s.get("invoice_rate")
                cost_val = s.get("invoice_cost")
            else:
                rate_val = s.get("tessie_rate")
                cost_val = s.get("tessie_cost")

            rate_str = f"${rate_val:.2f}" if rate_val else "-"
            cost_str = f"${cost_val:.2f}" if cost_val is not None else "$0.00"

            inv_str = s["invoice_number"] or "-"
            if len(inv_str) > widths[11] - 1:
                inv_str = inv_str[:widths[11] - 2] + "…"

            stat = s["status"]
            if "MATCHED" in stat:
                stat_styled = f"{C_GREEN}{stat}{C_RESET}"
            elif "RATE MISMATCH" in stat:
                stat_styled = f"{C_YELLOW}{stat}{C_RESET}"
                rate_str = f"{C_YELLOW}{rate_str}{C_RESET}"
            elif "COST MISMATCH" in stat or "WRONG" in stat:
                stat_styled = f"{C_RED}{stat}{C_RESET}"
                cost_str = f"{C_RED}{cost_str}{C_RESET}"
            elif "UNRECONCILED" in stat:
                stat_styled = f"{C_RED}{stat}{C_RESET}"
            elif "INVOICE ONLY" in stat:
                stat_styled = f"{C_BLUE}{stat}{C_RESET}"
            else:
                stat_styled = f"{C_DIM}{stat}{C_RESET}"

            row_str = "│" + "│".join([
                pad_display(idx_str + " ", widths[0], "right", truncate=True),
                pad_display(" " + dt_str, widths[1], "left", truncate=True),
                pad_display(f" {place_str}", widths[2], "left", truncate=True),
                pad_display(f" {net_str}", widths[3], "left", truncate=True),
                pad_display(soc_str + " ", widths[4], "right", truncate=True),
                pad_display(dur_str + " ", widths[5], "right", truncate=True),
                pad_display(f"{disp_str} ", widths[6], "right", truncate=True),
                pad_display(f"{bat_str} ", widths[7], "right", truncate=True),
                pad_display(eff_str + " ", widths[8], "right", truncate=True),
                pad_display(f"{rate_str} ", widths[9], "right", truncate=True),
                pad_display(f"{cost_str} ", widths[10], "right", truncate=True),
                pad_display(" " + inv_str, widths[11], "left", truncate=True),
                pad_display(f" {stat_styled}", widths[12], "left", truncate=True)
            ]) + "│"
            print(row_str)

        bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
        print(bot_b)

        bad_entries = [
            s for s in sessions 
            if "MISMATCH" in s.get("status", "") or "WRONG" in s.get("status", "")
        ]
        if bad_entries:
            print(f"  {C_YELLOW}⚠️  {len(bad_entries)} session(s) have cost or rate discrepancies with official invoices.{C_RESET}")
            print(f"  {C_DIM}Tip: Type 'u' or run --update-master to batch update all bad entries in master CSV.{C_RESET}")
        print()

    def print_correlation_table(self, filtered_sessions=None):
        sessions = filtered_sessions if filtered_sessions is not None else self.reconciled_sessions
        if not sessions:
            print(f"{C_YELLOW}No charging sessions matching the selected criteria.{C_RESET}")
            return

        # Prioritize sessions with invoices, superchargers, or detailed telemetry
        corr_sessions = [s for s in sessions if s.get("invoice_number") or s.get("is_supercharger") or s.get("detailed_telemetry")]
        if not corr_sessions:
            corr_sessions = sessions

        place_header_w = len("Place / Station")
        max_place_w = place_header_w
        for s in corr_sessions:
            clean_place = s["place_name"]
            if s.get("network"):
                clean_place = re.sub(rf"\s*\({re.escape(s['network'])}\)", "", clean_place, flags=re.IGNORECASE)
            p_len = display_len(f" {s['emoji']} {clean_place}") + 1
            if p_len > max_place_w:
                max_place_w = p_len

        place_col_width = max(23, max_place_w)

        headers = [
            "#", "Date / Time", "Place / Station", "Tessie Bat", "Tessie Car", "Invoice Disp", "Cable Loss", "Car Loss", "Total Loss", "Eff %", "Cost Var", "Telemetry"
        ]
        widths = [
            4, 17, place_col_width, 11, 11, 13, 11, 10, 11, 8, 10, 14
        ]
        total_inner_w = sum(widths) + len(widths) - 1

        print(f"┌{'─' * total_inner_w}┐")
        title = f" 📊 {C_BOLD}THREE-WAY CHARGING CORRELATION & LOSS AUDIT ({len(corr_sessions)} sessions){C_RESET}"
        print(f"│{pad_display(title, total_inner_w, 'left', truncate=True)}│")
        top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
        print(top_b)
        
        h_row = "│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers, widths)) + "│"
        print(h_row)

        mid_b = "├" + "┼".join("─" * w for w in widths) + "┤"
        print(mid_b)

        tot_bat = 0.0
        tot_car = 0.0
        tot_inv = 0.0
        tot_cable_loss = 0.0
        tot_car_loss = 0.0
        tot_loss = 0.0
        invoice_only_count = 0

        for s in corr_sessions:
            idx_str = str(s["charge_index"]) if s["charge_index"] is not None else "-"
            dt_str = s["datetime_str"][:16] if s["datetime_str"] else "-"
            
            clean_place = s["place_name"]
            if s.get("network"):
                clean_place = re.sub(rf"\s*\({re.escape(s['network'])}\)", "", clean_place, flags=re.IGNORECASE)
            place_str = f"{s['emoji']} {clean_place}".strip()

            bat_kwh = s.get("tessie_bat_kwh", s["battery_kwh"])
            car_kwh = s.get("tessie_car_kwh", s["dispenser_kwh"])
            inv_kwh = s.get("invoice_disp_kwh")
            
            bat_str = f"{bat_kwh:.2f} kWh"
            car_str = f"{car_kwh:.2f} kWh" if car_kwh > 0 else "-"
            inv_str = f"{inv_kwh:.2f} kWh" if inv_kwh else "-"

            cable_loss = s.get("cable_loss_kwh")
            car_loss = s.get("car_loss_kwh")
            t_loss = s.get("total_loss_kwh")
            no_telemetry = s.get("is_invoice_only_entry", False)
            if no_telemetry:
                invoice_only_count += 1

            cable_str = f"{cable_loss:.2f} kWh" if (inv_kwh and cable_loss is not None) else "-"
            car_str_loss = f"{car_loss:.2f} kWh" if (car_kwh > 0 and car_loss is not None) else "-"
            total_loss_str = f"{t_loss:.2f} kWh" if t_loss is not None else "N/A*"
            eff_str = f"{s['efficiency_pct']:.1f}%" if s.get("efficiency_pct") is not None else "N/A*"

            if s.get("invoice_cost") is not None and s.get("tessie_cost") is not None:
                d_cost = s["invoice_cost"] - s["tessie_cost"]
                cost_v = f"${d_cost:+.2f}"
            else:
                cost_v = "-"

            dt_obj = s.get("detailed_telemetry")
            if dt_obj:
                p_kw = dt_obj.get("peak_power_kw", 0)
                t_rise = dt_obj.get("temp_rise_c")
                if p_kw > 0 and t_rise is not None:
                    telem_str = f"{p_kw:.0f}kW/+{t_rise:.0f}°C"
                elif p_kw > 0:
                    telem_str = f"{p_kw:.0f} kW"
                else:
                    telem_str = f"{dt_obj.get('samples_count', 0)} pts"
            else:
                telem_str = "-"

            tot_bat += bat_kwh
            if car_kwh > 0 and not no_telemetry:
                tot_car += car_kwh
            if inv_kwh:
                tot_inv += inv_kwh
                if not no_telemetry:
                    tot_cable_loss += cable_loss
                    tot_car_loss += car_loss
                    tot_loss += t_loss

            row_str = "│" + "│".join([
                pad_display(idx_str + " ", widths[0], "right", truncate=True),
                pad_display(" " + dt_str, widths[1], "left", truncate=True),
                pad_display(f" {place_str}", widths[2], "left", truncate=True),
                pad_display(f"{bat_str} ", widths[3], "right", truncate=True),
                pad_display(f"{car_str} ", widths[4], "right", truncate=True),
                pad_display(f"{inv_str} ", widths[5], "right", truncate=True),
                pad_display(f"{cable_str} ", widths[6], "right", truncate=True),
                pad_display(f"{car_str_loss} ", widths[7], "right", truncate=True),
                pad_display(f"{total_loss_str} ", widths[8], "right", truncate=True),
                pad_display(eff_str + " ", widths[9], "right", truncate=True),
                pad_display(f"{cost_v} ", widths[10], "right", truncate=True),
                pad_display(telem_str + " ", widths[11], "right", truncate=True)
            ]) + "│"
            print(row_str)

        bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
        print(bot_b)
        if invoice_only_count:
            print(f"{C_DIM}  * N/A - {invoice_only_count} session(s) have no independent Tessie telemetry (invoice-only entries, appended from the invoice alone - see BUG-029); excluded from the loss/efficiency totals below.{C_RESET}")

        if tot_inv > 0:
            box_w = 145
            overall_eff = (tot_bat / tot_inv * 100.0)
            eff_color = C_GREEN if overall_eff >= 85.0 else (C_YELLOW if overall_eff >= 75.0 else C_RED)
            cable_eff = ((tot_inv - tot_cable_loss) / tot_inv * 100.0) if tot_inv > 0 else 100.0
            car_eff = (tot_bat / tot_car * 100.0) if tot_car > 0 else 100.0
            
            print()
            print(f"┌{'─' * (box_w - 2)}┐")
            s_title = f" 🔍 {C_BOLD}CORRELATION LOSS ANALYSIS BREAKDOWN{C_RESET}"
            print(f"│{pad_display(s_title, box_w - 2, 'left', truncate=True)}│")
            print(f"├{'─' * (box_w - 2)}┤")
            
            cl1 = f"  {C_BOLD}1. Dispenser Meter Output (Tesla Billed):{C_RESET} {tot_inv:,.2f} kWh"
            print(f"│{pad_display(cl1, box_w - 2, truncate=True)}│")
            
            cl2 = f"  {C_BOLD}2. Vehicle Gross Energy Intake:{C_RESET}           {tot_car:,.2f} kWh (Cable/Lead Loss: {tot_cable_loss:,.2f} kWh, {100-cable_eff:.1f}%)"
            print(f"│{pad_display(cl2, box_w - 2, truncate=True)}│")
            
            cl3 = f"  {C_BOLD}3. Net Battery Chemical Storage:{C_RESET}          {tot_bat:,.2f} kWh (Vehicle BMS Loss: {tot_car_loss:,.2f} kWh, {100-car_eff:.1f}%)"
            print(f"│{pad_display(cl3, box_w - 2, truncate=True)}│")
            
            cl4 = f"  {C_BOLD}Overall Efficiency & Total Loss:{C_RESET}          {eff_color}{overall_eff:.1f}% Net Efficiency{C_RESET} ({tot_loss:,.2f} kWh Total Loss)"
            print(f"│{pad_display(cl4, box_w - 2, truncate=True)}│")
            
            telem_linked = sum(1 for s in corr_sessions if s.get("detailed_telemetry"))
            telem_sources = set()
            for s in corr_sessions:
                dt_obj = s.get("detailed_telemetry")
                if dt_obj and dt_obj.get("source_path"):
                    telem_sources.add(shorten_display_path(os.path.dirname(dt_obj["source_path"])))
            telem_loc_str = f" from {', '.join(sorted(telem_sources))}" if telem_sources else ""
            cl5 = f"  {C_BOLD}High-Frequency Telemetry Files Linked:{C_RESET}    {telem_linked}/{len(corr_sessions)} sessions{telem_loc_str}"
            print(f"│{pad_display(cl5, box_w - 2, truncate=True)}│")
            print(f"└{'─' * (box_w - 2)}┘\n")

    @staticmethod
    def _estimate_20_80_time(timestamps, soc_values):
        """Estimate how long this session spent going from 20% to 80%
        SoC, interpolating between the two raw samples that bracket each
        threshold rather than snapping to the nearest sample. Returns a
        (minutes, note) tuple - note is None when both thresholds were
        cleanly observed, or an explanatory string when the session
        never actually reached one of them (started above 20%, or never
        reached 80%) so the caller can say so rather than print a
        confident-looking number for data that was never there."""
        pairs = []
        for ts, soc in zip(timestamps, soc_values):
            if not ts or soc is None:
                continue
            try:
                dt = datetime.strptime(ts.strip()[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            pairs.append((dt, soc))
        if len(pairs) < 2:
            return None, "not enough data"

        def crossing_time(threshold):
            if pairs[0][1] >= threshold:
                return pairs[0][0], True  # already past it at the first sample
            for (t0, s0), (t1, s1) in zip(pairs, pairs[1:]):
                if s0 < threshold <= s1:
                    frac = (threshold - s0) / (s1 - s0) if s1 != s0 else 0.0
                    return t0 + (t1 - t0) * frac, False
            return None, False

        t20, snapped_20 = crossing_time(20.0)
        t80, snapped_80 = crossing_time(80.0)
        if t20 is None:
            return None, f"never reached 20% (ended at {pairs[-1][1]:.0f}%)"
        if t80 is None:
            return None, f"never reached 80% (started at {pairs[0][1]:.0f}%, ended at {pairs[-1][1]:.0f}%)"
        mins = (t80 - t20).total_seconds() / 60.0
        note = None
        if snapped_20:
            note = f"session already at {pairs[0][1]:.0f}% at the first sample - 20% start is approximate"
        return mins, note

    def generate_charging_curve_chart(self, dt_rec, session=None):
        """Build the interactive charging-curve HTML for one deep-dive
        record (see AGENTS.md REQ-035/REQ-037) - Charger Power over time
        on the left axis, Battery Level % on the right, from the raw
        per-sample series parse_detailed_charge_csv() now keeps. `session`
        (a reconciled_sessions entry, optional) supplies the human-readable
        title (date + place/network) and subtitle lines (20-80% estimate,
        cost) - without it this falls back to the bare filename-based
        title, e.g. for a caller that only has the deep-dive dict. Returns
        the output path, or None if there's nothing plottable (e.g. an
        old-format deep-dive dict from before raw_samples existed)."""
        raw = dt_rec.get("raw_samples")
        if not raw or not raw.get("timestamp"):
            return None

        timestamps = raw["timestamp"]
        n = len(timestamps)
        # Use a short HH:MM:SS label for the x-axis rather than the full
        # "Timestamp (AEST)" string - this is a single session, the date
        # is already in the title, repeating it on every tick is just noise.
        x_labels = []
        for ts in timestamps:
            if ts and len(ts) >= 19:
                x_labels.append(ts[11:19])
            else:
                x_labels.append(ts or "")

        series = [
            {
                "name": "Charger Power",
                "unit": "kW",
                "axis": "left",
                "color": "#2563eb",
                "values": raw.get("charger_power_kw", [None] * n),
            },
            {
                "name": "Battery Level",
                "unit": "%",
                "axis": "right",
                "color": "#f59e0b",
                "values": raw.get("battery_level_pct", [None] * n),
            },
        ]

        # No "plots" subfolder - the chart now sits right beside the CSV
        # it was built from, alongside every other deep-dive artifact for
        # this same session (see AGENTS.md REQ-037).
        src_dir = os.path.dirname(dt_rec.get("source_path") or "") or os.getcwd()
        base = os.path.splitext(os.path.basename(dt_rec.get("source_file") or "session"))[0]
        out_path = os.path.join(src_dir, f"{base}_curve.html")

        if session:
            dt = session.get("datetime")
            date_str = f"{dt.day} {dt.strftime('%b %Y')}" if dt else ""
            place = session.get("place_name") or ""
            network = session.get("network") or ""
            net_part = f" ({network})" if network and network.lower() not in place.lower() else ""
            title = f"{date_str} - {place}{net_part}".strip(" -")
            if not title:
                title = f"Charging Curve - {dt_rec.get('source_file', '')}"
        else:
            title = f"Charging Curve - {dt_rec.get('source_file', '')}"

        subtitle_lines = [
            f"{dt_rec.get('samples_count', n)} samples over "
            f"{dt_rec.get('duration_mins', 0):.1f} min - peak "
            f"{dt_rec.get('peak_power_kw', 0):.1f} kW, avg "
            f"{dt_rec.get('avg_power_kw', 0):.1f} kW"
        ]

        mins_20_80, note_20_80 = self._estimate_20_80_time(timestamps, raw.get("battery_level_pct") or [])
        if mins_20_80 is not None:
            line = f"Est. 20% → 80%: {mins_20_80:.1f} min"
            if note_20_80:
                line += f" ({note_20_80})"
            subtitle_lines.append(line)
        elif note_20_80:
            subtitle_lines.append(f"20% → 80%: {note_20_80}")

        if session:
            kwh = session.get("invoice_disp_kwh") or session.get("dispenser_kwh") or session.get("battery_kwh")
            cost = session.get("invoice_cost") if session.get("invoice_cost") is not None else session.get("tessie_cost")
            rate = session.get("invoice_rate") if session.get("invoice_rate") is not None else session.get("tessie_rate")
            if kwh and cost is not None:
                rate_part = f" (@ ${rate:.2f}/kWh)" if rate else ""
                subtitle_lines.append(f"{kwh:.2f} kWh for ${cost:.2f} AUD{rate_part}")

        subtitle = "<br>".join(subtitle_lines)

        write_interactive_chart(
            out_path,
            title=title,
            x_values=x_labels,
            x_label="Time",
            series=series,
            subtitle=subtitle,
        )
        return out_path

    def get_energy_source_crosscheck(self, s, force_refresh=False):
        """Cross-checks one Evnex home-charging session's tariff/cost
        figures against AGL's real smart-meter data and PVOutput's real
        solar generation (AGENTS.md TODO-027) - replacing
        personal_chargers.json's static rate-card guess (what
        get_expected_tariff_rate() above already returns) with the same
        real-source computation TODO-013's ev_session_cost.py already
        does in batch across all 139 Evnex sessions, just triggered live
        here for the ONE session inspect_session() is showing, and
        cached to disk afterward.

        Only meaningful for a home Evnex session (s["network"] ==
        "Evnex") - AGL/PVOutput are whole-household data sources, so a
        public DC Supercharger session has nothing to cross-check
        against and this returns None immediately for those.

        Caching contract, per the user's own three-part ask: (a) fetch
        on the fly if the sibling "<evnex .json stem>_sources.json"
        cache file next to that session's own already-synced
        charge_deepdive_*_Evnex_Home*.json (evnex_api_sync.py) doesn't
        exist yet, (b) use it unchanged if it does, (c) force_refresh=True
        re-fetches and only overwrites the cache once the new result
        writes successfully - a backup of the OLD cache is kept until
        the new write is confirmed, and restored if the write itself
        fails, so a bad refresh falls back to the original file rather
        than losing it (a failure merely deleting that backup afterward
        is not treated as a write failure - only a failure to WRITE the
        new file triggers the restore).

        Returns a dict (cached or freshly computed), or None if this
        isn't an Evnex session, no matching Evnex deep-dive JSON can be
        found for it, or the real AGL tariff-rates file isn't present on
        this machine (agl_demand_calculator.load_rate_periods() - a
        real, gitignored, per-account file; its absence is a normal
        "not configured on this machine" case, not an error)."""
        if s.get("network") != "Evnex":
            return None

        try:
            import ev_session_cost as _evc
            from agl_demand_calculator import load_rate_periods
        except Exception:
            return None

        s_dt = s.get("datetime")
        if not s_dt:
            return None
        s_end = s_dt + timedelta(minutes=s.get("duration_mins", 0) or 0)

        # Find this session's own Evnex deep-dive JSON by WINDOW OVERLAP
        # (Evnex's [charging_started_utc, charging_stopped_utc] vs this
        # Tessie session's own [start, start+duration]), not by how close
        # the two start times are - same interval-overlap pattern
        # match_detailed_drive() already uses for pairing drive telemetry,
        # and for the same real reason: Tessie logs each separate
        # start/stop "charging" event of what is, physically, one longer
        # plugged-in Evnex session (the charger pausing/resuming around
        # its own solar-wait threshold) as its OWN short master-CSV row -
        # a 4-7min, <1kWh Tessie blip can sit entirely INSIDE a much
        # longer real Evnex session that started up to an hour earlier.
        # An earlier version of this method matched on start-time
        # proximity alone (a tight +/-10min window) and silently found
        # NOTHING for exactly this shape of session - caught live against
        # two real examples (#190, #195) that the user flagged as still
        # showing only the old registry-estimate rate, with no obvious
        # error - both turned out to overlap a real Evnex session
        # starting 60-66 minutes earlier once checked by window instead
        # of by start-time gap. Deliberately NOT datetime.fromtimestamp()
        # (naive, silently uses whatever timezone the PROCESS happens to
        # be running in - UTC on this session's own device-bridge shell,
        # not Australia/Sydney) - converts through the session's own
        # recorded IANA zone instead, same pattern evaluate_session()
        # below already uses.
        try:
            evnex_sessions = _evc.load_evnex_deepdive_sessions()
        except Exception:
            evnex_sessions = []
        pad = timedelta(minutes=2)
        matched = None
        for es in evnex_sessions:
            es_start_epoch = _evc.iso_to_epoch(es.get("charging_started_utc"))
            if es_start_epoch is None:
                continue
            es_end_epoch = _evc.iso_to_epoch(es.get("charging_stopped_utc")) or es_start_epoch
            try:
                es_tz = ZoneInfo(es.get("timezone") or "Australia/Sydney")
            except Exception:
                es_tz = ZoneInfo("Australia/Sydney")
            es_start_dt = datetime.fromtimestamp(es_start_epoch, tz=timezone.utc).astimezone(es_tz).replace(tzinfo=None)
            es_end_dt = datetime.fromtimestamp(es_end_epoch, tz=timezone.utc).astimezone(es_tz).replace(tzinfo=None)
            if es_start_dt <= s_end + pad and es_end_dt >= s_dt - pad:
                matched = es
                break
        if not matched:
            return None

        evnex_path = matched["_file"]
        cache_path = evnex_path[:-5] + "_sources.json" if evnex_path.endswith(".json") else evnex_path + "_sources.json"

        if os.path.isfile(cache_path) and not force_refresh:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                cached["_from_cache"] = True
                return cached
            except Exception:
                pass  # unreadable cache - fall through and recompute

        try:
            periods = load_rate_periods()
        except Exception:
            return None

        try:
            result = _evc.evaluate_session(matched, periods)
        except Exception:
            return None

        # PVOutput cross-check - best-effort, never fatal to the
        # AGL/Evnex figures above if it fails (missing credentials,
        # network egress, rate limit, an unexpected response shape).
        pv_generation_kwh = None
        pv_self_consumed_kwh = None
        pv_error = None
        try:
            from pvoutput_common import (
                load_config as _pv_load_config,
                get_api_key as _pv_get_api_key,
                get_system_id as _pv_get_system_id,
                api_get as _pv_api_get,
                parse_status_rows as _pv_parse_status_rows,
            )
            pv_cfg = _pv_load_config()
            pv_key = _pv_get_api_key(None, pv_cfg)
            pv_sid = _pv_get_system_id(None, pv_cfg)
            if not pv_key or not pv_sid:
                pv_error = "no PVOutput credentials configured"
            else:
                date_str = result["local_start"].strftime("%Y%m%d")
                status, body, _hdrs = _pv_api_get(
                    "getstatus", pv_key, pv_sid, params={"d": date_str, "h": 1, "limit": 288}
                )
                if status != 200:
                    pv_error = f"HTTP {status}"
                else:
                    rows = _pv_parse_status_rows(body)
                    # Basic h=1 row shape: Date,Time,EnergyGen(Wh),
                    # PowerGen(W),EnergyCons(Wh),PowerCons(W),
                    # Normalised,Temp,Voltage (Tessie/PVOUTPUT_API.md) -
                    # Energy Generation is a CUMULATIVE-for-the-day
                    # figure, so this session's own share is the delta
                    # between the last reading at/after local_end and
                    # the last reading at/before local_start, not a sum.
                    gen_readings = []
                    for row in rows:
                        if len(row) < 3:
                            continue
                        try:
                            r_time = datetime.strptime(f"{row[0]} {row[1]}", "%Y%m%d %H:%M")
                            r_gen_wh = float(row[2])
                        except (ValueError, IndexError):
                            continue
                        gen_readings.append((r_time, r_gen_wh))
                    gen_readings.sort()
                    before = [g for t, g in gen_readings if t <= result["local_start"]]
                    upto_end = [g for t, g in gen_readings if t <= result["local_end"]]
                    if before and upto_end and upto_end[-1] >= before[-1]:
                        pv_generation_kwh = (upto_end[-1] - before[-1]) / 1000.0
                    elif gen_readings:
                        pv_error = "not enough readings spanning this session's window"
                    else:
                        pv_error = "no readings returned for this date"
                    if pv_generation_kwh is not None and result.get("real_export_kwh") is not None:
                        pv_self_consumed_kwh = max(0.0, pv_generation_kwh - result["real_export_kwh"])
        except Exception as e:
            pv_error = str(e)

        result_out = dict(result)
        result_out["local_start"] = result["local_start"].isoformat()
        result_out["local_end"] = result["local_end"].isoformat()
        result_out["pv_generation_kwh"] = pv_generation_kwh
        result_out["pv_self_consumed_kwh"] = pv_self_consumed_kwh
        result_out["pv_error"] = pv_error
        result_out["evnex_deepdive_file"] = os.path.basename(evnex_path)
        result_out["computed_at"] = datetime.now().isoformat()
        result_out["_from_cache"] = False

        # Write-then-swap: a failed WRITE restores the previous cache
        # unchanged; a failed cleanup of the backup afterward is just a
        # harmless leftover .bak file, never a reason to roll back data
        # that was already written successfully.
        backup_path = cache_path + ".bak"
        had_old = os.path.isfile(cache_path)
        write_ok = False
        try:
            if had_old:
                shutil.copy2(cache_path, backup_path)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result_out, f, indent=2, default=str)
            write_ok = True
        except Exception:
            write_ok = False

        if write_ok:
            if had_old and os.path.isfile(backup_path):
                try:
                    os.remove(backup_path)
                except Exception:
                    pass
        elif had_old and os.path.isfile(backup_path):
            try:
                shutil.move(backup_path, cache_path)
            except Exception:
                pass

        return result_out

    def inspect_session(self, target):
        if not self.reconciled_sessions:
            self.reconcile()

        target_session = None
        try:
            target_idx = int(target)
            for s in self.reconciled_sessions:
                if s["charge_index"] == target_idx:
                    target_session = s
                    break
        except ValueError:
            t_dt = parse_flexible_date(target)
            if t_dt:
                for s in self.reconciled_sessions:
                    if s["datetime"] and abs((s["datetime"] - t_dt).total_seconds()) < 7200:
                        target_session = s
                        break

        if not target_session:
            print(f"{C_RED}Could not find charging session matching:{C_RESET} {target}")
            return

        s = target_session
        box_w = 145
        idx_label = f"#{s['charge_index']}" if s["charge_index"] is not None else "-"
        header_title = f" ⚡ {C_BOLD}DEEP-DIVE CHARGING INSPECTION: {idx_label} {s['place_name']}{C_RESET}"
        print()
        print(f"┌{'─' * (box_w - 2)}┐")
        print(f"│{pad_display(header_title, box_w - 2, 'left', truncate=True)}│")
        print(f"├{'─' * (box_w - 2)}┤")
        
        net_suffix = f" ({s['network']})" if s['network'] and s['network'].lower() not in s['place_name'].lower() else ""
        l1 = f"  {C_BOLD}Location / Station:{C_RESET}   {s['emoji']} {s['place_name']}{net_suffix}"
        print(f"│{pad_display(l1, box_w - 2, truncate=True)}│")
        
        loc_raw = s["raw_charge"]["location_raw"] if s["raw_charge"] else "-"
        l2 = f"  {C_BOLD}Raw Address:{C_RESET}          {loc_raw}"
        print(f"│{pad_display(l2, box_w - 2, truncate=True)}│")
        
        l3 = f"  {C_BOLD}Started At (AEST):{C_RESET}    {s['datetime_str']} (Duration: {s['duration_mins']:.0f} mins)"
        print(f"│{pad_display(l3, box_w - 2, truncate=True)}│")

        l4 = f"  {C_BOLD}Battery SoC Range:{C_RESET}    {s['start_soc']}% ➔ {s['end_soc']}% (+{s['end_soc'] - s['start_soc']}%)"
        print(f"│{pad_display(l4, box_w - 2, truncate=True)}│")

        l5 = f"  {C_BOLD}Rated Range Added:{C_RESET}    +{s['range_added_km']:.1f} km  │  {C_BOLD}Odometer:{C_RESET} {s['odometer_km']:,.2f} km"
        print(f"│{pad_display(l5, box_w - 2, truncate=True)}│")

        print(f"├{'─' * (box_w - 2)}┤")
        
        e_title = f"  {C_BOLD}{C_MAGENTA}⚡ THREE-WAY ENERGY RECONCILIATION & LOSS AUDIT:{C_RESET}"
        print(f"│{pad_display(e_title, box_w - 2, truncate=True)}│")

        lw_loss = 31
        if s.get("invoice_disp_kwh"):
            inv_net = (s.get("matched_invoice", {}) or {}).get("network") or s.get("network") or "Dispenser"
            l6a = f"    • {C_BOLD}{'1. Invoice Meter (Dispenser):':<{lw_loss}}{C_RESET}{s['invoice_disp_kwh']:.2f} kWh ({inv_net} Billed Dispenser Meter)"
            print(f"│{pad_display(l6a, box_w - 2, truncate=True)}│")

        if s.get("is_invoice_only_entry"):
            # BUG-029: this session has no independent Tessie telemetry at
            # all - "Energy Added"/"Energy Used" in the master CSV are both
            # just the same invoice figure copied twice (see
            # append_charge_to_master()), never separately measured. Showing
            # a fabricated "2./3." breakdown here would imply a verified
            # 3-way match that never happened.
            l6c = f"    • {C_BOLD}{'2./3. Vehicle Intake / Battery Storage:':<{lw_loss}}{C_RESET}N/A"
            print(f"│{pad_display(l6c, box_w - 2, truncate=True)}│")
            l6d = f"    {C_YELLOW}⚠️  No independent Tessie measurement for this session - invoice-only entry (this charge predates Tessie API history and was appended from the invoice alone).{C_RESET}"
            for w_line in wrap_text_display(l6d, box_w - 4):
                print(f"│{pad_display(w_line, box_w - 2, truncate=True)}│")
        else:
            car_in = s.get("tessie_car_kwh", 0.0)
            if car_in > 0:
                l6b = f"    • {C_BOLD}{'2. Vehicle Gross Intake:':<{lw_loss}}{C_RESET}{car_in:.2f} kWh (Electricity Consumed by Car)"
                print(f"│{pad_display(l6b, box_w - 2, truncate=True)}│")

            l7 = f"    • {C_BOLD}{'3. Net Battery Storage (BMS):':<{lw_loss}}{C_RESET}{s['battery_kwh']:.2f} kWh (Net Battery Pack Chemical Storage)"
            print(f"│{pad_display(l7, box_w - 2, truncate=True)}│")

            if s.get("invoice_disp_kwh") and s.get("cable_loss_kwh") is not None:
                l8a = f"    • {C_BOLD}{'Dispenser & Cable Loss:':<{lw_loss}}{C_RESET}{s['cable_loss_kwh']:.2f} kWh (Stall electronics & cable resistance)"
                print(f"│{pad_display(l8a, box_w - 2, truncate=True)}│")
                l8b = f"    • {C_BOLD}{'Vehicle Conditioning Loss:':<{lw_loss}}{C_RESET}{s['car_loss_kwh']:.2f} kWh (BMS, chiller pumps & heat dissipation)"
                print(f"│{pad_display(l8b, box_w - 2, truncate=True)}│")
                if s.get("car_overshoot_kwh", 0.0) > 0:
                    # BUG-030: the car's own reported intake exceeded what
                    # the dispenser/invoice billed - a measurement
                    # disagreement, not a real negative loss. Surfaced
                    # explicitly instead of being silently clamped to 0.00.
                    l8c = f"    {C_YELLOW}⚠️  Vehicle reported {s['car_overshoot_kwh']:.2f} kWh MORE intake than the dispenser billed - meter disagreement, not a real 0.00 kWh cable loss.{C_RESET}"
                    for w_line in wrap_text_display(l8c, box_w - 4):
                        print(f"│{pad_display(w_line, box_w - 2, truncate=True)}│")

            eff_color = C_GREEN if s["efficiency_pct"] >= 85.0 else (C_YELLOW if s["efficiency_pct"] >= 75.0 else C_RED)
            l8 = f"    • {C_BOLD}{'Total Charging Loss:':<{lw_loss}}{C_RESET}{s['loss_kwh']:.2f} kWh  ({eff_color}{s['efficiency_pct']:.1f}% Dispenser-to-Battery{C_RESET})"
            print(f"│{pad_display(l8, box_w - 2, truncate=True)}│")

        curve_path = None
        # Detailed high-frequency telemetry section if available
        dt_rec = s.get("detailed_telemetry")
        if dt_rec:
            print(f"├{'─' * (box_w - 2)}┤")
            short_src = shorten_display_path(dt_rec['source_file'], 85)
            t_title = f"  {C_BOLD}{C_CYAN}🔋 HIGH-FREQUENCY TELEMETRY AUDIT ({short_src}):{C_RESET}"
            print(f"│{pad_display(t_title, box_w - 2, truncate=True)}│")
            
            lw_tele = 30
            t1 = f"    • {C_BOLD}{'High-Res Telemetry Samples:':<{lw_tele}}{C_RESET}{dt_rec['samples_count']} readings over {dt_rec['duration_mins']:.1f} mins"
            print(f"│{pad_display(t1, box_w - 2, truncate=True)}│")
            
            t2 = f"    • {C_BOLD}{'Charging Power Profile:':<{lw_tele}}{C_RESET}Peak: {dt_rec['peak_power_kw']:.1f} kW  │  Average: {dt_rec['avg_power_kw']:.1f} kW"
            print(f"│{pad_display(t2, box_w - 2, truncate=True)}│")
            
            if dt_rec.get("initial_batt_temp_c") is not None and dt_rec.get("final_batt_temp_c") is not None:
                t3 = f"    • {C_BOLD}{'Battery Module Temp:':<{lw_tele}}{C_RESET}{dt_rec['initial_batt_temp_c']:.1f}°C ➔ {dt_rec['final_batt_temp_c']:.1f}°C (Peak: {dt_rec['max_batt_temp_c']:.1f}°C, ΔT: +{dt_rec['temp_rise_c']:.1f}°C)"
                print(f"│{pad_display(t3, box_w - 2, truncate=True)}│")
                
            heater_str = "ACTIVE (Preheating)" if dt_rec.get("battery_heater") else "Off (Active cooling loop)"
            amb_str = f"{dt_rec['outside_temp_c']:.1f}°C" if dt_rec.get("outside_temp_c") is not None else "-"
            t4 = f"    • {C_BOLD}{'Thermal Management:':<{lw_tele}}{C_RESET}Heater: {heater_str}  │  Ambient Temp: {amb_str}"
            print(f"│{pad_display(t4, box_w - 2, truncate=True)}│")
            
            if dt_rec.get("delta_energy_remaining_kwh") is not None:
                t5 = f"    • {C_BOLD}{'Energy Remaining Delta:':<{lw_tele}}{C_RESET}{dt_rec['energy_remaining_start']:.2f} kWh ➔ {dt_rec['energy_remaining_end']:.2f} kWh (+{dt_rec['delta_energy_remaining_kwh']:.2f} kWh)"
                print(f"│{pad_display(t5, box_w - 2, truncate=True)}│")
                
            if dt_rec.get("integrated_dispenser_kwh", 0) > 0:
                t6 = f"    • {C_BOLD}{'Integrated Telemetry Energy:':<{lw_tele}}{C_RESET}Port: {dt_rec['integrated_dispenser_kwh']:.2f} kWh  │  Battery Pack (V×I): {dt_rec['integrated_pack_kwh']:.2f} kWh"
                print(f"│{pad_display(t6, box_w - 2, truncate=True)}│")

            # REQ-035: an interactive charging-curve chart, built only when
            # the deep-dive record actually has raw per-sample data (older
            # dt_rec dicts / any parse failure mean raw_samples is absent -
            # generate_charging_curve_chart() returns None in that case and
            # this whole block is skipped, no error, no placeholder line).
            try:
                curve_path = self.generate_charging_curve_chart(dt_rec, session=s)
            except Exception:
                curve_path = None
            if curve_path:
                t7 = f"    • {C_BOLD}{'Charging Curve:':<{lw_tele}}{C_RESET}{shorten_display_path(curve_path, 85)}"
                print(f"│{pad_display(t7, box_w - 2, truncate=True)}│")

        print(f"├{'─' * (box_w - 2)}┤")
        
        c_title = f"  {C_BOLD}{C_GREEN}💰 FINANCIAL & TARIFF AUDIT:{C_RESET}"
        print(f"│{pad_display(c_title, box_w - 2, truncate=True)}│")

        lw_fin = 27
        l9 = f"    • {C_BOLD}{'Tessie Logged Cost:':<{lw_fin}}{C_RESET}${s['tessie_cost']:.2f} AUD (@ ${s['tessie_rate']:.2f}/kWh)"
        print(f"│{pad_display(l9, box_w - 2, truncate=True)}│")

        if s["invoice_number"]:
            matched_inv = s.get("matched_invoice") if isinstance(s.get("matched_invoice"), dict) else {}
            inv_net_name = matched_inv.get("network") or s["network"]
            inv_type_label = ("Tesla Tax Invoice" if (s["is_supercharger"] or "tesla" in inv_net_name.lower()) else f"{inv_net_name} Receipt") + ":"
            rate_details = f"@ ${s['invoice_rate']:.2f}/kWh"
            if s.get("invoice_net_rate") and s.get("invoice_net_rate") != s.get("invoice_rate"):
                rate_details = f"@ ${s['invoice_rate']:.2f}/kWh gross [${s['invoice_net_rate']:.2f} ex-GST]"
            l10 = f"    • {C_BOLD}{inv_type_label:<{lw_fin}}{C_RESET}${s['invoice_cost']:.2f} AUD (Inv #{s['invoice_number']} {rate_details})"
            print(f"│{pad_display(l10, box_w - 2, truncate=True)}│")
            
            delta_cost = (s["invoice_cost"] or 0) - s["tessie_cost"]
            d_color = C_GREEN if abs(delta_cost) < 0.02 else (C_YELLOW if abs(delta_cost) < 1.0 else C_RED)
            l11 = f"    • {C_BOLD}{'Cost Reconciliation Delta:':<{lw_fin}}{C_RESET}{d_color}${delta_cost:+.2f} AUD{C_RESET}"
            print(f"│{pad_display(l11, box_w - 2, truncate=True)}│")

            if s.get("invoice_rate") is not None and s.get("tessie_rate") is not None:
                delta_rate = s["tessie_rate"] - s["invoice_rate"]
                if abs(delta_rate) >= 0.01:
                    l11b = f"    • {C_BOLD}{'Rate Reconciliation Delta:':<{lw_fin}}{C_RESET}{C_YELLOW}${delta_rate:+.2f}/kWh (CSV: ${s['tessie_rate']:.2f} vs Inv: ${s['invoice_rate']:.2f}){C_RESET}"
                    print(f"│{pad_display(l11b, box_w - 2, truncate=True)}│")
        else:
            l10 = f"    • {C_BOLD}{'Tax Invoice / Receipt:':<{lw_fin}}{C_RESET}{C_YELLOW}No matching invoice file found in configured invoices directory{C_RESET}"
            print(f"│{pad_display(l10, box_w - 2, truncate=True)}│")

        if s.get("expected_rate") is not None:
            arch_tag = f" {C_MAGENTA}[Historical Archive]{C_RESET}" if s.get("is_archived_tariff") else ""
            sched_label = f" [{s.get('expected_schedule_name')}]" if s.get("expected_schedule_name") else ""
            tz_label = f" (TZ: {s.get('timezone', 'Australia/Sydney')})"
            note_l = f"    {C_DIM}(what our own registry's rate card says this SHOULD cost, for cross-checking against the real cost above - not what was actually billed){C_RESET}"
            for w_line in wrap_text_display(note_l, box_w - 4):
                print(f"│{pad_display(w_line, box_w - 2, truncate=True)}│")
            l12 = f"    • {C_BOLD}{'Expected Tariff Rate:':<{lw_fin}}{C_RESET}${s['expected_rate']:.2f}/kWh{sched_label}{tz_label}{arch_tag}"
            print(f"│{pad_display(l12, box_w - 2, truncate=True)}│")

            if s.get("theoretical_cost") is not None:
                th_gst_str = f" (incl. ${s['theoretical_gst']:.2f} GST [10%])" if s.get("theoretical_gst") is not None else ""
                l12b = f"    • {C_BOLD}{'Theoretical Tariff Cost:':<{lw_fin}}{C_RESET}${s['theoretical_cost']:.2f} AUD{th_gst_str}"
                print(f"│{pad_display(l12b, box_w - 2, truncate=True)}│")

                real_cost = s.get("invoice_cost") if s.get("invoice_cost") is not None else s.get("tessie_cost")
                if real_cost is not None:
                    tariff_delta = s["theoretical_cost"] - real_cost
                    if abs(tariff_delta) >= 1.0:
                        l12c = f"    {C_RED}⚠️  Tariff Audit Delta: ${tariff_delta:+.2f} - the registry's rate card disagrees with what was actually billed by more than rounding; the registry data (not the real cost) is the more likely thing to be wrong here.{C_RESET}"
                        for w_line in wrap_text_display(l12c, box_w - 4):
                            print(f"│{pad_display(w_line, box_w - 2, truncate=True)}│")
        else:
            l12 = f"    • {C_BOLD}{'Expected Tariff Rate:':<{lw_fin}}{C_RESET}{C_YELLOW}No tariff rate configured in registry{C_RESET}"
            print(f"│{pad_display(l12, box_w - 2, truncate=True)}│")

        # AGENTS.md TODO-027: for a home Evnex session, cross-check the
        # figures above against AGL's real smart-meter data and
        # PVOutput's real solar generation instead of relying only on
        # personal_chargers.json's rough rate card. get_energy_source_
        # crosscheck() returns None immediately for anything that isn't
        # an Evnex session, so this section simply doesn't print for a
        # DC/Supercharger inspection.
        xcheck = self.get_energy_source_crosscheck(s) if s.get("network") == "Evnex" else None
        if xcheck:
            print(f"├{'─' * (box_w - 2)}┤")
            x_title = f"  {C_BOLD}{C_CYAN}🔎 ENERGY SOURCE CROSS-CHECK (AGL + Evnex + PVOutput):{C_RESET}"
            print(f"│{pad_display(x_title, box_w - 2, truncate=True)}│")
            lw_x = 34
            x1 = f"    • {C_BOLD}{'AGL Tariff Rate (real, dated):':<{lw_x}}{C_RESET}${xcheck['usage_rate']:.2f}/kWh ex GST (period from {xcheck['period_effective_from']})"
            print(f"│{pad_display(x1, box_w - 2, truncate=True)}│")
            # GST-inclusive, to match "Theoretical Tariff Cost" above (also
            # GST-inclusive) - a real gap caught by the user manually
            # reproducing this calculation by hand (AGENTS.md TODO-027):
            # these two dollar figures used to sit right next to each
            # other in this same box while being ex-GST and incl-GST
            # respectively, with nothing saying so. .get() with a
            # same-session fallback so an older cache file written before
            # this fix (no *_incl_gst keys yet) still displays correctly
            # rather than KeyError-ing.
            base_cost_incl_gst = xcheck.get("base_cost_incl_gst")
            if base_cost_incl_gst is None:
                base_cost_incl_gst = xcheck["base_cost"] * 1.10
            x2 = f"    • {C_BOLD}{'AGL Base Cost (100% grid):':<{lw_x}}{C_RESET}${base_cost_incl_gst:.4f} AUD incl GST"
            print(f"│{pad_display(x2, box_w - 2, truncate=True)}│")
            if xcheck.get("solar_pct") is not None:
                adj_incl_gst = xcheck.get("evnex_adjusted_cost_incl_gst")
                if adj_incl_gst is None:
                    # Pre-fix cache: same GST-free-on-solar-share treatment
                    # evaluate_session() now applies, reconstructed here
                    # from the ex-GST split this cache file does have.
                    solar_frac = xcheck["solar_pct"] / 100.0
                    grid_ex_gst = xcheck["evnex_adjusted_cost"] - xcheck["kwh_total"] * solar_frac * xcheck["solar_tier1_rate"]
                    adj_incl_gst = grid_ex_gst * 1.10 + xcheck["kwh_total"] * solar_frac * xcheck["solar_tier1_rate"]
                x3 = f"    • {C_BOLD}{'Evnex Solar Split:':<{lw_x}}{C_RESET}{xcheck['solar_pct']:.1f}% solar / {100 - xcheck['solar_pct']:.1f}% grid -> ${adj_incl_gst:.4f} AUD incl GST"
            else:
                x3 = f"    • {C_BOLD}{'Evnex Solar Split:':<{lw_x}}{C_RESET}{C_YELLOW}n/a (no distributionByTariff on this session){C_RESET}"
            print(f"│{pad_display(x3, box_w - 2, truncate=True)}│")
            x4 = (f"    • {C_BOLD}{'Real AGL Import/Export (window):':<{lw_x}}{C_RESET}"
                  f"{xcheck['real_import_kwh']:.2f} kWh import / {xcheck['real_export_kwh']:.2f} kWh export "
                  f"({xcheck['buckets_found']}/{xcheck['buckets_found'] + xcheck['buckets_missing']} half-hour buckets found)")
            print(f"│{pad_display(x4, box_w - 2, truncate=True)}│")
            if xcheck.get("pv_generation_kwh") is not None:
                x5 = (f"    • {C_BOLD}{'PVOutput Generation (window):':<{lw_x}}{C_RESET}"
                      f"{xcheck['pv_generation_kwh']:.2f} kWh generated -> {xcheck['pv_self_consumed_kwh']:.2f} kWh self-consumed")
            else:
                x5 = f"    • {C_BOLD}{'PVOutput Generation (window):':<{lw_x}}{C_RESET}{C_YELLOW}unavailable ({xcheck.get('pv_error') or 'unknown reason'}){C_RESET}"
            print(f"│{pad_display(x5, box_w - 2, truncate=True)}│")
            if xcheck.get("conflict"):
                x6 = f"    {C_RED}⚠️  Conflict: Evnex claims mostly-solar but real AGL grid import is large relative to this session's own kWh - see AGENTS.md TODO-013.{C_RESET}"
                for w_line in wrap_text_display(x6, box_w - 4):
                    print(f"│{pad_display(w_line, box_w - 2, truncate=True)}│")
            cache_note = "cached - press [r] to refresh" if xcheck.get("_from_cache") else "freshly fetched just now"
            x7 = f"    {C_DIM}({cache_note}){C_RESET}"
            print(f"│{pad_display(x7, box_w - 2, truncate=True)}│")

        if "MATCHED" in s['status'] or "VERIFIED" in s['status']:
            stat_color = C_GREEN
        elif "RATE MISMATCH" in s['status']:
            stat_color = C_YELLOW
        elif "COST MISMATCH" in s['status'] or "WRONG" in s['status']:
            stat_color = C_RED
        elif "INVOICE ONLY" in s['status']:
            stat_color = C_BLUE
        else:
            stat_color = C_RED

        l13 = f"    • {C_BOLD}{'Reconciliation Status:':<{lw_fin}}{C_RESET}{stat_color}{s['status']}{C_RESET}"
        print(f"│{pad_display(l13, box_w - 2, truncate=True)}│")
        print(f"└{'─' * (box_w - 2)}┘\n")

        if sys.stdin.isatty():
            has_inv = bool(s.get("invoice_number") and s.get("invoice_cost") is not None)
            has_xcheck = bool(xcheck)
            has_curve = bool(curve_path and os.path.exists(curve_path))
            try:
                actions = []
                if has_curve:
                    actions.append("[o]pen chart in browser")
                if has_inv:
                    actions.append("[u]pdate master CSV from invoice")
                actions.append("[m]anually correct")
                if has_xcheck:
                    actions.append("[r]efresh energy sources (AGL/Evnex/PVOutput)")
                actions.append("[Enter] to return")
                prompt_actions = ", ".join(actions) + ": "
                act = input(f"{C_BOLD}Action:{C_RESET} {prompt_actions}").strip().lower()
                if act in ['o', 'open', 'chart', 'c'] and has_curve:
                    try_open_in_browser(curve_path)
                    self.inspect_session(target)
                elif act in ['u', 'update', 'apply', 'sync'] and has_inv:
                    self.apply_invoice_to_session(s)
                elif act in ['m', 'manual', 'e', 'edit']:
                    self.prompt_manual_correction(s)
                elif act in ['r', 'refresh'] and has_xcheck:
                    print(f"\n{C_DIM}Refreshing AGL/Evnex/PVOutput energy sources...{C_RESET}")
                    self.get_energy_source_crosscheck(s, force_refresh=True)
                    self.inspect_session(target)
            except (KeyboardInterrupt, EOFError):
                print()

    def print_tessie_sync_instructions(self, s, cost, rate):
        raw = s.get("raw_charge") or {}
        s_dt = s.get("datetime")
        s_at_str = raw.get("started_at_str") or s.get("datetime_str") or ""
        e_at_str = raw.get("ended_at_str") or s.get("ended_at_str") or ""
        place = s.get("place_name") or raw.get("location_raw", "")
        date_only = s_at_str.split()[0] if s_at_str else (s_dt.strftime("%Y-%m-%d") if s_dt else "")
        
        box_w = 78
        print(f"\n{C_ORANGE}┌{'─' * (box_w - 2)}┐{C_RESET}")
        header = f" 📲 {C_BOLD}ACTION REQUIRED: UPDATE TESSIE TO MATCH THIS CHARGE{C_RESET}"
        print(f"{C_ORANGE}│{pad_display(header, box_w - 2, 'left', truncate=True)}│{C_RESET}")
        print(f"{C_ORANGE}├{'─' * (box_w - 2)}┤{C_RESET}")
        l_info1 = "  Tessie stores costs in its cloud database. To sync Tessie:"
        print(f"│{pad_display(l_info1, box_w - 2, 'left')}│")
        print(f"│{' ' * (box_w - 2)}│")
        l_step1 = f"  1. Open Tessie App or Web: {C_CYAN}https://dash.tessie.com/charging{C_RESET}"
        print(f"│{pad_display(l_step1, box_w - 2, 'left')}│")
        print(f"│{' ' * (box_w - 2)}│")
        l_step2 = "  2. Locate charging session:"
        print(f"│{pad_display(l_step2, box_w - 2, 'left')}│")
        l_d = f"     • Date:        {C_BOLD}{date_only}{C_RESET}"
        print(f"│{pad_display(l_d, box_w - 2, 'left')}│")
        l_t = f"     • Time Window: {s_at_str} ➔ {e_at_str or 'Session End'}"
        print(f"│{pad_display(l_t, box_w - 2, 'left')}│")
        l_p = f"     • Location:    {place}"
        print(f"│{pad_display(l_p, box_w - 2, 'left')}│")
        print(f"│{' ' * (box_w - 2)}│")
        l_step3 = "  3. Update Cost:"
        print(f"│{pad_display(l_step3, box_w - 2, 'left')}│")
        l_c = f"     • Total Cost:   {C_GREEN}{C_BOLD}${cost:.2f} AUD{C_RESET}"
        print(f"│{pad_display(l_c, box_w - 2, 'left')}│")
        l_r = f"     • Unit Rate:    {C_GREEN}{C_BOLD}${rate:.2f}/kWh{C_RESET}"
        print(f"│{pad_display(l_r, box_w - 2, 'left')}│")
        print(f"{C_ORANGE}└{'─' * (box_w - 2)}┘{C_RESET}\n")

    def apply_invoice_to_session(self, s):
        """Updates master CSV and charges files with the matched official invoice cost and rate."""
        inv_cost = s.get("invoice_cost")
        inv_rate = s.get("invoice_rate")
        disp_kwh = s.get("invoice_disp_kwh") or s.get("battery_kwh")
        if inv_rate is None and inv_cost is not None and disp_kwh and disp_kwh > 0:
            inv_rate = round(inv_cost / disp_kwh, 2)
            
        print(f"\n\033[96m────────────────────────────────────────────────────────────────────────\033[0m")
        print(f"\033[96m📥 UPDATE MASTER CSV WITH INVOICE #{s['invoice_number']}\033[0m")
        print(f"   Station:          {s['emoji']} {s['place_name']} ({s['datetime_str']})")
        print(f"   Current Telemetry: Rate: ${s['tessie_rate']:.2f}/kWh | Cost: ${s['tessie_cost']:.2f}")
        print(f"   Official Invoice:  Rate: ${inv_rate:.2f}/kWh | Cost: ${inv_cost:.2f} AUD (Billed: {disp_kwh:.2f} kWh)")
        print(f"\033[96m────────────────────────────────────────────────────────────────────────\033[0m")
        
        try:
            confirm = input(f"Save Invoice Cost (${inv_cost:.2f}) & Gross Rate (${inv_rate:.2f}/kWh) to master CSV? [Y/n]: ").strip().lower()
            if confirm not in ['n', 'no']:
                raw = s.get("raw_charge") or {}
                s_at = raw.get("started_at_str") or s.get("datetime_str")
                loc = raw.get("location_raw") or s.get("place_name")
                
                count = self.patch_charge_record(s_at, loc, inv_cost, inv_rate)
                print(f"\033[92m✔ Updated {count} file(s) in master charging records.\033[0m")
                self.print_tessie_sync_instructions(s, inv_cost, inv_rate)
                self.load_charges()
                self.reconcile(interactive=False)
                self.inspect_session(s["charge_index"])
        except (ValueError, KeyboardInterrupt, EOFError):
            print("\nUpdate cancelled.")

    def prompt_manual_correction(self, s):
        """Allows interactive manual correction of Cost Per kWh and Cost when no invoice is present."""
        print(f"\n{C_ORANGE}────────────────────────────────────────────────────────────────────────{C_RESET}")
        print(f"{C_ORANGE}✏️  MANUALLY CORRECT CHARGING SESSION #{s['charge_index']}{C_RESET}")
        print(f"   Station:              {s['emoji']} {s['place_name']} ({s['datetime_str']})")
        print(f"   Battery Energy Added: {s['battery_kwh']:.2f} kWh (Preserved telemetry)")
        print(f"   Current Telemetry:    Rate: ${s['tessie_rate']:.2f}/kWh | Cost: ${s['tessie_cost']:.2f}")
        print(f"{C_ORANGE}────────────────────────────────────────────────────────────────────────{C_RESET}")
        
        try:
            rate_in = input(f"Enter Gross Rate ($/kWh) [press Enter to keep ${s['tessie_rate']:.2f}]: ").strip()
            if rate_in:
                new_rate = float(rate_in.replace("$", ""))
            else:
                new_rate = s['tessie_rate']

            calc_cost = round(s['battery_kwh'] * new_rate, 2)
            cost_in = input(f"Enter Gross Total Cost ($) [press Enter for ${calc_cost:.2f}]: ").strip()
            if cost_in:
                new_cost = float(cost_in.replace("$", ""))
            else:
                new_cost = calc_cost

            confirm = input(f"Save Gross Rate: ${new_rate:.2f}/kWh and Gross Cost: ${new_cost:.2f}? [Y/n]: ").strip().lower()
            if confirm not in ['n', 'no']:
                raw = s.get("raw_charge") or {}
                s_at = raw.get("started_at_str") or s.get("datetime_str")
                loc = raw.get("location_raw") or s.get("place_name")
                
                count = self.patch_charge_record(s_at, loc, new_cost, new_rate)
                print(f"\033[92m✔ Updated {count} file(s). Reloading charges...\033[0m\n")
                self.print_tessie_sync_instructions(s, new_cost, new_rate)
                self.load_charges()
                self.reconcile(interactive=False)
                
                # Re-inspect to display updated card
                self.inspect_session(s["charge_index"])
        except (ValueError, KeyboardInterrupt, EOFError):
            print("\nCorrection cancelled.")

    def append_charge_to_master(self, s, cost, rate):
        """Appends an invoice-only charging session to charges_master.csv."""
        inv = s.get("matched_invoice") or {}
        inv_dt = inv.get("date") or s.get("datetime")
        s_at = inv_dt.strftime("%Y-%m-%d %H:%M") if inv_dt else (s.get("datetime_str") or "")
        e_at = s_at
        loc = inv.get("location_raw") or s.get("place_name") or ""
        saved_loc = s.get("place_name") or ""
        is_sc = "true" if s.get("is_supercharger") else "false"
        is_fc = "true" if s.get("is_fast_charger") else "false"
        kwh = inv.get("energy_kwh") or s.get("dispenser_kwh") or 0.0

        new_row = {
            "Started At (AEST)": s_at,
            "Ended At (AEST)": e_at,
            "Duration (Minutes)": "0",
            "Location": loc,
            "Saved Location": saved_loc,
            "Latitude": "",
            "Longitude": "",
            "Supercharger": is_sc,
            "Fast Charger": is_fc,
            "Odometer (km)": "",
            "Energy Added (kWh)": f"{kwh:.2f}",
            "Energy Used (kWh)": f"{kwh:.2f}",
            "Rated Range Added (km)": "",
            "Starting Battery (%)": "",
            "Ending Battery (%)": "",
            "Cost": f"{float(cost):.2f}",
            "Cost Per kWh": f"{float(rate):.2f}"
        }

        appended_count = 0
        search_dirs = list(self.charges_dirs) + list(self.tessie_dirs)
        for td in search_dirs:
            master_file = os.path.join(td, "charges_master.csv")
            if os.path.exists(master_file):
                try:
                    with open(master_file, "r", encoding="utf-8", errors="ignore") as f:
                        header_line = f.readline().strip()
                        fields = [h.strip() for h in header_line.split(",") if h.strip()]
                    with open(master_file, "a", encoding="utf-8", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
                        writer.writerow(new_row)
                    appended_count += 1
                except Exception:
                    pass
        return appended_count

    def update_master_from_invoices(self, auto_confirm=False):
        """Scans all reconciled sessions and updates master CSV for any discrepancies against invoices."""
        if not self.reconciled_sessions:
            self.reconcile()
            
        candidates = []
        for s in self.reconciled_sessions:
            if s.get("invoice_number") and s.get("invoice_cost") is not None:
                i_cost = s["invoice_cost"]
                i_rate = s.get("invoice_rate")
                t_cost = s.get("tessie_cost", 0)
                t_rate = s.get("tessie_rate", 0)
                if abs(i_cost - t_cost) >= 0.01 or (i_rate is not None and abs(i_rate - t_rate) >= 0.01):
                    candidates.append((s, i_cost, i_rate))
                    
        if not candidates:
            print(f"\n{C_GREEN}✔ All charges in master CSV already match their official invoices.{C_RESET}\n")
            return
            
        print(f"\n\033[96m==========================================================================\033[0m")
        print(f"\033[96m📥 FOUND {len(candidates)} CHARGING SESSION(S) TO UPDATE FROM OFFICIAL INVOICES\033[0m")
        print(f"\033[96m==========================================================================\033[0m")
        for idx, (s, i_cost, i_rate) in enumerate(candidates, 1):
            disp_kwh = s.get("invoice_disp_kwh") or s.get("dispenser_kwh") or s.get("battery_kwh") or 0.0
            t_cost = s.get("tessie_cost", 0.0)
            t_rate = s.get("tessie_rate", 0.0)
            if s.get("raw_charge") is None:
                tag = f"{C_BLUE}[Missing from CSV ➔ Append]{C_RESET}"
            elif abs(i_cost - t_cost) >= 0.01 and abs(i_rate - t_rate) >= 0.01:
                tag = f"{C_RED}[Cost & Rate Mismatch ➔ Fix CSV]{C_RESET}"
            elif abs(i_cost - t_cost) >= 0.01:
                tag = f"{C_RED}[Cost Mismatch ➔ Fix CSV Cost]{C_RESET}"
            else:
                tag = f"{C_YELLOW}[Rate Mismatch ➔ Fix CSV Rate]{C_RESET}"

            print(f"[{idx}] {s['datetime_str']} @ {s['place_name']} {tag}")
            print(f"    Current CSV:   ${t_cost:.2f} (@ ${t_rate:.2f}/kWh)")
            print(f"    Invoice #{s['invoice_number']}: ${i_cost:.2f} (@ ${i_rate:.2f}/kWh on {disp_kwh:.2f} kWh)")
            
        if not auto_confirm:
            try:
                ans = input(f"\nUpdate master CSV for all {len(candidates)} session(s)? [y/N]: ").strip().lower()
                if ans not in ['y', 'yes']:
                    print("Update cancelled.")
                    return
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                return
                
        updated_total = 0
        appended_total = 0
        for s, i_cost, i_rate in candidates:
            raw = s.get("raw_charge")
            if raw:
                s_at = raw.get("started_at_str") or s.get("datetime_str")
                loc = raw.get("location_raw") or s.get("place_name")
                cnt = self.patch_charge_record(s_at, loc, i_cost, i_rate)
                updated_total += cnt
            else:
                cnt = self.append_charge_to_master(s, i_cost, i_rate)
                appended_total += cnt
            
        summary_parts = []
        if updated_total:
            summary_parts.append(f"updated {len(candidates) - (1 if appended_total else 0)} charge record(s) across {updated_total} file(s)")
        if appended_total:
            summary_parts.append(f"appended missing session to {appended_total} master file(s)")
        print(f"\n{C_GREEN}✔ Successfully {', and '.join(summary_parts)} in master charging records.{C_RESET}")
        
        box_w = 95
        print(f"\n{C_ORANGE}┌{'─' * (box_w - 2)}┐{C_RESET}")
        head = f" 📲 {C_BOLD}ACTION REQUIRED: UPDATE CHARGES IN TESSIE TO MATCH OFFICIAL INVOICES{C_RESET}"
        print(f"{C_ORANGE}│{pad_display(head, box_w - 2, 'left', truncate=True)}│{C_RESET}")
        print(f"{C_ORANGE}├{'─' * (box_w - 2)}┤{C_RESET}")
        print(f"│  Go to Tessie App -> Charges (or https://dash.tessie.com/charging) and update costs:       │")
        print(f"│                                                                                             │")
        for s, i_cost, i_rate in candidates:
            raw = s.get("raw_charge") or {}
            s_at = raw.get("started_at_str") or s.get("datetime_str") or ""
            dt_part = s_at.split()[0] if s_at else ""
            pl = s.get("place_name") or ""
            line_str = f"  • Date: {dt_part:<10} │ {s_at} @ {pl[:24]:<24} │ Set: ${i_cost:.2f} (@ ${i_rate:.2f}/kWh)"
            print(f"│{pad_display(line_str, box_w - 2, 'left', truncate=True)}│")
        print(f"{C_ORANGE}└{'─' * (box_w - 2)}┘{C_RESET}\n")
        self.load_charges()
        self.reconcile(interactive=False)

    def list_chargers(self):
        print(f"\n{C_YELLOW}┌────────────────────────────────────────────────────────────────────────────────────────┐{C_RESET}")
        print(f"{C_YELLOW}│ ℹ️  CHARGER DISCOVERY & SEARCH NOTICE                                                    │{C_RESET}")
        print(f"{C_YELLOW}├────────────────────────────────────────────────────────────────────────────────────────┤{C_RESET}")
        print(f"│ Charger discovery, searching, pricing comparison, and TOU rate lookups have moved to  │")
        print(f"│ the dedicated unified discovery tool: {C_BOLD}./Tools/find_chargers.py{C_RESET}                       │")
        print(f"│                                                                                        │")
        print(f"│ Usage Examples:                                                                        │")
        print(f"│   • Search nearby:          ./Tools/find_chargers.py --near 'Ryde'                     │")
        print(f"│   • Tesla Superchargers:    ./Tools/find_chargers.py --sc                              │")
        print(f"│   • Specific network:       ./Tools/find_chargers.py --network chargefox               │")
        print(f"│   • Sort by active price:   ./Tools/find_chargers.py --sort price                      │")
        print(f"│   • Export master JSON:     ./Tools/find_chargers.py --export-consolidated             │")
        print(f"{C_YELLOW}└────────────────────────────────────────────────────────────────────────────────────────┘{C_RESET}\n")

        sc_list = list(self.superchargers.items())
        widths = [4, 7, 28, 28, 8, 6, 12, 28]
        total_w = sum(widths) + len(widths) - 1

        print(f"\n┌{'─' * total_w}┐")
        sc_title = f" 🔴 {C_BOLD}REGISTERED TESLA SUPERCHARGERS ({len(sc_list)} stations){C_RESET}"
        print(f"│{pad_display(sc_title, total_w, 'left')}│")
        print("├" + "┬".join("─" * w for w in widths) + "┤")
        headers = ["#", "State", "Station Name", "General Location / Suburb", "Stalls", "Tier", "Access", "Rates (AUD)"]
        print("│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers, widths)) + "│")
        print("├" + "┼".join("─" * w for w in widths) + "┤")

        for idx, (name, data) in enumerate(sc_list, 1):
            meta = data.get("tesla_metadata", {})
            loc = data.get("location", {})
            hw = data.get("hardware", {})
            comp = data.get("compatibility", {})
            cost = data.get("tessie_cost_config", {})
            st_state = loc.get("state") or (name.split(",")[1].strip() if "," in name else "-")
            suburb = loc.get("suburb") or meta.get("general_location") or "-"
            
            schedules = cost.get("rate_schedules", [])
            sched_str = f"{len(schedules)} TOU periods" if len(schedules) > 1 else (f"${schedules[0].get('rate_per_kwh')}/kWh" if schedules else f"${cost.get('per_kwh_flat', 0):.2f}/kWh flat")
            access_str = f"{C_GREEN}CCS2 All{C_RESET}" if comp.get("open_to_non_tesla") else "Tesla Only"
            stalls_str = f"{hw.get('stalls')} bays" if hw.get('stalls') else "-"
            tier_str = hw.get("tier", "-") or "-"

            row_cells = [
                pad_display(str(idx) + " ", widths[0], "right"),
                pad_display(st_state, widths[1], "center"),
                pad_display(f" {name}", widths[2], "left", truncate=True),
                pad_display(f" {suburb}", widths[3], "left", truncate=True),
                pad_display(stalls_str + " ", widths[4], "right"),
                pad_display(tier_str, widths[5], "center"),
                pad_display(" " + access_str, widths[6], "left"),
                pad_display(f" {sched_str}", widths[7], "left", truncate=True)
            ]
            print("│" + "│".join(row_cells) + "│")
        print("└" + "┴".join("─" * w for w in widths) + "┘\n")

        oth_list = list(self.charging_stations.items())
        widths_oth = [4, 7, 28, 22, 10, 10, 12, 28]
        total_w_oth = sum(widths_oth) + len(widths_oth) - 1

        print(f"┌{'─' * total_w_oth}┐")
        oth_title = f" 🏢 {C_BOLD}REGISTERED TESLA DESTINATION & HOME CHARGERS ({len(oth_list)} stations){C_RESET}"
        print(f"│{pad_display(oth_title, total_w_oth, 'left')}│")
        print("├" + "┬".join("─" * w for w in widths_oth) + "┤")
        headers_oth = ["#", "State", "Station / Location Name", "Network / Operator", "Type", "Power", "Hardware", "Rates (AUD)"]
        print("│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers_oth, widths_oth)) + "│")
        print("├" + "┼".join("─" * w for w in widths_oth) + "┤")

        for idx, (name, data) in enumerate(oth_list, 1):
            st_type = data.get("type") or data.get("tesla_metadata", {}).get("type", "ac")
            is_dest = "destination" in str(st_type).lower() or "destination" in name.lower()
            emoji = "🏠" if st_type == "home" else ("🅿️" if is_dest else "🔌")
            hw = data.get("hardware", {})
            costs = data.get("costs", {}) or data.get("tessie_cost_config", {})
            
            if st_type == "home":
                net = "Home AC"
            elif is_dest:
                net = "Tesla Destination"
            else:
                net = data.get("network") or data.get("operator") or "Tesla Destination"

            type_lbl = "Home AC" if st_type == "home" else ("Tesla Dest" if is_dest else "AC Public")
            pwr_val = hw.get("max_power_kw") or hw.get("maxPowerKw") or 22.0
            pwr = f"{float(pwr_val):.0f} kW" if pwr_val else "-"
            hw_type = hw.get("tier") or hw.get("charger_type") or "Type 2"
            
            if "per_kwh_flat" in costs and costs.get("per_kwh_flat") is not None:
                sched_str = f"${costs.get('per_kwh_flat', 0):.2f}/kWh flat" if costs.get("per_kwh_flat") > 0 else "Free"
            elif "flat_per_kwh" in costs:
                sched_str = f"${costs.get('flat_per_kwh', 0):.2f}/kWh flat" if costs.get('flat_per_kwh') > 0 else "Free"
            else:
                sched_str = "Free"

            loc = data.get("location", {}) if isinstance(data.get("location"), dict) else {}
            st_state = loc.get("state") or data.get("state")
            if (not st_state or st_state == "-") and loc.get("lat") and loc.get("lon"):
                try:
                    from find_tesla_chargers import state_from_coords
                    resolved_st, _ = state_from_coords(loc.get("lat"), loc.get("lon"))
                    if resolved_st:
                        st_state = resolved_st
                except Exception:
                    pass
            st_state = st_state or "-"

            row_cells = [
                pad_display(str(idx) + " ", widths_oth[0], "right"),
                pad_display(st_state, widths_oth[1], "center"),
                pad_display(f" {emoji} {name}", widths_oth[2], "left", truncate=True),
                pad_display(f" {net}", widths_oth[3], "left", truncate=True),
                pad_display(" " + type_lbl, widths_oth[4], "left"),
                pad_display(pwr + " ", widths_oth[5], "right"),
                pad_display(" " + hw_type, widths_oth[6], "left"),
                pad_display(f" {sched_str}", widths_oth[7], "left", truncate=True)
            ]
            print("│" + "│".join(row_cells) + "│")
        print("└" + "┴".join("─" * w for w in widths_oth) + "┘\n")

        # Table 3: PlugShare & 3rd-Party Multi-Network Registries
        if self.plugshare_chargers:
            ps_list = list(self.plugshare_chargers.items())
            print(f"┌{'─' * total_w_oth}┐")
            ps_title = f" 🌐 {C_BOLD}REGISTERED MULTI-NETWORK PUBLIC CHARGERS ({len(ps_list)} stations — Chargefox, Evie, Exploren, AmpCharge, BP Pulse, NRMA){C_RESET}"
            print(f"│{pad_display(ps_title, total_w_oth, 'left')}│")
            print("├" + "┬".join("─" * w for w in widths_oth) + "┤")
            print("│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers_oth, widths_oth)) + "│")
            print("├" + "┼".join("─" * w for w in widths_oth) + "┤")

            for idx, (name, data) in enumerate(ps_list, 1):
                ps_meta = data.get("plugshare_metadata", {}) if isinstance(data.get("plugshare_metadata"), dict) else {}
                loc = data.get("location", {}) if isinstance(data.get("location"), dict) else {}
                hw = data.get("hardware", {}) if isinstance(data.get("hardware"), dict) else {}
                tariffs = data.get("tariffs", {}) if isinstance(data.get("tariffs"), dict) else {}
                
                net = ps_meta.get("network") or data.get("network") or "3rd-Party"
                pwr_val = hw.get("max_power_kw") or data.get("max_power_kw") or 0.0
                is_fast = float(pwr_val) >= 25.0
                type_lbl = "DC Fast" if is_fast else "AC Public"
                emoji = "⚡" if "tesla" in net.lower() or "nrma" in net.lower() or "exploren" in net.lower() else ("🦊" if "chargefox" in net.lower() else ("🔌" if "evie" in net.lower() else ("🟢" if "bp" in net.lower() else "🔴")))
                
                pwr = f"{float(pwr_val):.0f} kW" if pwr_val else "-"
                conns = hw.get("connector_types") or []
                hw_type = conns[0] if conns else (hw.get("tier") or "-")
                
                flat_rate = tariffs.get("per_kwh_flat") or tariffs.get("rate_per_kwh")
                if flat_rate is not None and float(flat_rate) > 0:
                    sched_str = f"${float(flat_rate):.2f}/kWh"
                elif flat_rate == 0.0 or tariffs.get("pricing_model") == "free":
                    sched_str = "Free"
                else:
                    sched_str = "-"

                st_state = loc.get("state") or data.get("state") or "-"

                row_cells = [
                    pad_display(str(idx) + " ", widths_oth[0], "right"),
                    pad_display(st_state, widths_oth[1], "center"),
                    pad_display(f" {emoji} {name}", widths_oth[2], "left", truncate=True),
                    pad_display(f" {net}", widths_oth[3], "left", truncate=True),
                    pad_display(" " + type_lbl, widths_oth[4], "left"),
                    pad_display(pwr + " ", widths_oth[5], "right"),
                    pad_display(" " + hw_type, widths_oth[6], "left"),
                    pad_display(f" {sched_str}", widths_oth[7], "left", truncate=True)
                ]
                print("│" + "│".join(row_cells) + "│")
            print("└" + "┴".join("─" * w for w in widths_oth) + "┘\n")

    def export_reconciliation(self, filepath, filtered_sessions=None):
        if not self.reconciled_sessions:
            self.reconcile()

        sessions = filtered_sessions if filtered_sessions is not None else self.reconciled_sessions

        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".json":
            with open(filepath, "w", encoding="utf-8") as f:
                json_data = []
                for s in sessions:
                    item = dict(s)
                    item["datetime"] = s["datetime"].isoformat() if s["datetime"] else None
                    item.pop("raw_charge", None)
                    item.pop("matched_invoice", None)
                    json_data.append(item)
                json.dump(json_data, f, indent=2)
        else:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                fieldnames = [
                    "Charge Index", "Started At", "Place", "Network", "Start SoC (%)", "End SoC (%)",
                    "Duration (Mins)", "Dispenser Energy (kWh)", "Battery Energy (kWh)", "Loss (kWh)",
                    "Efficiency (%)", "Tessie Cost ($)", "Tessie Rate ($/kWh)", "Invoice Cost ($)",
                    "Invoice Rate ($/kWh)", "Invoice Number", "Status"
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for s in sessions:
                    writer.writerow({
                        "Charge Index": s["charge_index"] or "",
                        "Started At": s["datetime_str"],
                        "Place": s["place_name"],
                        "Network": s["network"],
                        "Start SoC (%)": s["start_soc"],
                        "End SoC (%)": s["end_soc"],
                        "Duration (Mins)": f"{s['duration_mins']:.0f}",
                        "Dispenser Energy (kWh)": f"{s['dispenser_kwh']:.2f}",
                        "Battery Energy (kWh)": f"{s['battery_kwh']:.2f}",
                        "Loss (kWh)": f"{s['loss_kwh']:.2f}" if s.get("loss_kwh") is not None else "N/A",
                        "Efficiency (%)": f"{s['efficiency_pct']:.1f}" if s.get("efficiency_pct") is not None else "N/A",
                        "Tessie Cost ($)": f"{s['tessie_cost']:.2f}",
                        "Tessie Rate ($/kWh)": f"{s['tessie_rate']:.2f}",
                        "Invoice Cost ($)": f"{s['invoice_cost']:.2f}" if s["invoice_cost"] is not None else "",
                        "Invoice Rate ($/kWh)": f"{s['invoice_rate']:.2f}" if s["invoice_rate"] is not None else "",
                        "Invoice Number": s["invoice_number"] or "",
                        "Status": s["status"]
                    })
        print(f"{C_GREEN}Successfully exported {len(sessions)} reconciled records to:{C_RESET} {filepath}")


    def rename_invoices(self, format_pattern=None, dry_run=False, auto_confirm=False):
        if not self._loaded:
            self.load_charges()
            self.load_invoices()
            self.reconcile()


        # Discover all PDF files in self.invoice_dirs with path deduplication
        pdf_files = []
        seen_pdf_paths = set()
        for inv_dir in self.invoice_dirs:
            if not os.path.isdir(inv_dir):
                continue
            for root, _, files in os.walk(inv_dir):
                for f in sorted(files):
                    if f.startswith(".") or not f.lower().endswith(".pdf"):
                        continue
                    full_p = os.path.join(root, f)
                    real_p = os.path.realpath(full_p)
                    if real_p in seen_pdf_paths:
                        continue
                    seen_pdf_paths.add(real_p)
                    pdf_files.append(full_p)

        if not pdf_files:
            print(f"{C_YELLOW}No PDF invoice files found in configured directories.{C_RESET}\n")
            return

        rename_plan = []
        for fpath in pdf_files:
            fname = os.path.basename(fpath)
            parsed = TeslaInvoiceParser.parse_invoice_file(fpath)
            if not parsed or not isinstance(parsed, dict):
                continue

            inv_num = parsed.get("invoice_number") or "INV"
            inv_date = parsed.get("date")
            inv_cost = parsed.get("total_cost")
            inv_loc = parsed.get("location_raw", "")

            # Match against Tessie charging sessions
            matched_session = None
            for s in self.reconciled_sessions:
                s_dt = s.get("datetime")
                if not s_dt or not inv_date:
                    continue
                if s.get("invoice_number") and s.get("invoice_number") == inv_num:
                    matched_session = s
                    break
                if s_dt.date() == inv_date.date():
                    if inv_cost is None or abs(s.get("tessie_cost", 0) - inv_cost) < 0.10:
                        matched_session = s
                        break

            # Format timestamp (YYYYMMDD_HHMM for clean chronological sorting)
            if matched_session and matched_session.get("datetime"):
                dt_str = matched_session["datetime"].strftime("%Y%m%d_%H%M")
            elif inv_date:
                if inv_date.hour != 0 or inv_date.minute != 0:
                    dt_str = inv_date.strftime("%Y%m%d_%H%M")
                else:
                    dt_str = inv_date.strftime("%Y%m%d_0000")
            else:
                dt_str = datetime.now().strftime("%Y%m%d_0000")

            # Determine network slug (Tesla, Exploren, Chargefox, Evie, etc.)
            net_raw = parsed.get("network") or (matched_session.get("network") if matched_session else None) or "Tesla"
            net_raw_lower = str(net_raw).lower()
            if "supercharger" in net_raw_lower or "tesla" in net_raw_lower:
                net_slug = "Tesla"
            elif "exploren" in net_raw_lower:
                net_slug = "Exploren"
            elif "chargefox" in net_raw_lower:
                net_slug = "Chargefox"
            elif "evie" in net_raw_lower:
                net_slug = "Evie"
            elif "bp" in net_raw_lower:
                net_slug = "BP_Pulse"
            elif "ampcharge" in net_raw_lower or "ampol" in net_raw_lower:
                net_slug = "AmpCharge"
            elif "jolt" in net_raw_lower:
                net_slug = "Jolt"
            elif "nrma" in net_raw_lower:
                net_slug = "NRMA"
            else:
                net_slug = re.sub(r"[^A-Za-z0-9]+", "_", net_raw).strip("_")

            # Clean location string / short_name
            clean_loc = ""
            reg_obj = matched_session.get("registry_obj") if matched_session else None
            # Use the registry entry that was actually EFFECTIVE at the
            # invoice's own date, not whichever one is current today - a
            # station renamed since (e.g. West Gosford, NSW -> Gosford, NSW
            # on 2026-09-13) must keep its old short_name on invoices from
            # before that rename. Fixed 2026-09-14, BUG-063, AGENTS.local.md.
            effective_dt = (matched_session.get("datetime") if matched_session else None) or inv_date
            if reg_obj and isinstance(reg_obj, dict) and effective_dt:
                place_name_for_lookup = matched_session.get("place_name") if matched_session else None
                reg_obj, _ = self.resolve_effective_registry_entry(reg_obj, effective_dt, place_name=place_name_for_lookup)
            if reg_obj and isinstance(reg_obj, dict):
                clean_loc = reg_obj.get("tesla_metadata", {}).get("short_name", "")
                if not clean_loc:
                    nm = reg_obj.get("name", "")
                    nm = re.sub(r"\s*\(.*?\)", "", nm).strip()
                    clean_loc = re.sub(r"[^A-Za-z0-9]+", "_", nm).strip("_")

            if not clean_loc and inv_loc:
                inv_clean_lower = inv_loc.lower().strip()
                for sc_k, sc_v in self.superchargers.items():
                    sc_meta = sc_v.get("tesla_metadata", {})
                    sc_name_clean = sc_k.lower().strip()
                    kws = [k.lower() for k in sc_meta.get("keywords", [])]
                    if inv_clean_lower == sc_name_clean or any(k in inv_clean_lower for k in kws) or sc_name_clean in inv_clean_lower:
                        # Same effective-date resolution as the registry_obj
                        # branch above, and for the same reason: this is the
                        # path that was ACTUALLY firing for the West
                        # Gosford, NSW invoices (matched_session's own
                        # registry_obj was None for them, so execution fell
                        # straight through to this current-registry keyword
                        # match without ever consulting the archive). Fixed
                        # 2026-09-14, BUG-063, AGENTS.local.md.
                        effective_sc_v = sc_v
                        if effective_dt:
                            effective_sc_v, _ = self.resolve_effective_registry_entry(sc_v, effective_dt, place_name=sc_k)
                        clean_loc = effective_sc_v.get("tesla_metadata", {}).get("short_name", "") or sc_meta.get("short_name", "")
                        break

            if not clean_loc and inv_loc:
                inv_clean_lower = inv_loc.lower().strip()
                for p_name, p_data in self.places.items():
                    p_name_clean = p_name.lower().strip()
                    kws = [k.lower() for k in p_data.get("keywords", [])]
                    if inv_clean_lower == p_name_clean or any(k in inv_clean_lower for k in kws) or p_name_clean in inv_clean_lower:
                        clean_loc = re.sub(r"[^A-Za-z0-9]+", "_", p_name).strip("_")
                        break

            if not clean_loc:
                loc_source = (matched_session.get("place_name") if matched_session else "") or inv_loc
                loc_source = re.sub(r"\s*\(.*?\)", "", loc_source).strip()
                clean_loc = re.sub(r"[^A-Za-z0-9]+", "_", loc_source).strip("_")
                clean_loc = re.sub(r"_+", "_", clean_loc)

            if not clean_loc:
                clean_loc = "Charging"

            # Apply naming pattern: date/time network invoice_num place
            if format_pattern:
                target_fname = format_pattern.format(
                    timestamp=dt_str,
                    datetime=dt_str,
                    date_time=dt_str,
                    YYYYMMDD_HHMM=dt_str,
                    network=net_slug,
                    Network=net_slug,
                    invoice_num=inv_num,
                    invoice=inv_num,
                    location=clean_loc,
                    Location=clean_loc,
                    place=clean_loc,
                    Place=clean_loc
                )
            else:
                target_fname = f"{dt_str}_{net_slug}_{inv_num}_{clean_loc}.pdf"

            if not target_fname.lower().endswith(".pdf"):
                target_fname += ".pdf"

            target_path = os.path.join(os.path.dirname(fpath), target_fname)
            already_named = (fname == target_fname)

            rename_plan.append({
                "old_path": fpath,
                "old_name": fname,
                "new_path": target_path,
                "new_name": target_fname,
                "invoice_number": inv_num,
                "date_str": dt_str,
                "network": net_slug,
                "location": clean_loc,
                "cost": inv_cost,
                "already_named": already_named
            })

        to_rename_count = sum(1 for item in rename_plan if not item["already_named"])
        if to_rename_count == 0:
            print(f"{C_GREEN}✔ All {len(rename_plan)} invoice files are already correctly named!{C_RESET}\n")
            return

        # Print preview table with balanced widths for Current and Proposed Filenames
        widths = [4, 65, 65, 16, 18, 16]
        total_inner_w = sum(widths) + len(widths) - 1

        print(f"\n┌{'─' * total_inner_w}┐")
        print(f"│{pad_display(f' ⚡ {C_BOLD}CHARGING INVOICE RENAMER ({to_rename_count} file(s) to rename){C_RESET}', total_inner_w, 'left')}│")
        top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
        print(top_b)
        h = "│" + "│".join([
            pad_display(" #", widths[0]),
            pad_display(" Current Filename", widths[1]),
            pad_display(" Proposed Target Filename", widths[2]),
            pad_display(" Date / Time", widths[3]),
            pad_display(" Invoice #", widths[4]),
            pad_display(" Status", widths[5])
        ]) + "│"
        print(h)
        mid_b = "├" + "┼".join("─" * w for w in widths) + "┤"
        print(mid_b)

        for idx, item in enumerate(rename_plan, 1):
            if item["already_named"]:
                st_badge = f"{C_GREEN}ALREADY NAMED{C_RESET}"
            else:
                st_badge = f"{C_CYAN}RENAME ➔{C_RESET}"

            old_disp = item["old_name"] if len(item["old_name"]) <= widths[1] - 2 else item["old_name"][:widths[1] - 5] + "..."
            new_disp = item["new_name"] if len(item["new_name"]) <= widths[2] - 2 else item["new_name"][:widths[2] - 5] + "..."
            inv_disp = item["invoice_number"] if len(item["invoice_number"]) <= widths[4] - 2 else item["invoice_number"][:widths[4] - 5] + "..."

            row_str = "│" + "│".join([
                pad_display(f" {idx}", widths[0]),
                pad_display(f" {old_disp}", widths[1]),
                pad_display(f" {new_disp}", widths[2]),
                pad_display(f" {item['date_str']}", widths[3]),
                pad_display(f" {inv_disp}", widths[4]),
                pad_display(f" {st_badge}", widths[5])
            ]) + "│"
            print(row_str)

        bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
        print(bot_b)
        print()

        if dry_run:
            print(f"{C_YELLOW}[DRY RUN]{C_RESET} {to_rename_count} files would be renamed. No files were modified.\n")
            return

        if not auto_confirm:
            try:
                resp = input(f"Proceed with renaming {to_rename_count} files? [y/N]: ").strip().lower()
                if resp not in ["y", "yes"]:
                    print(f"{C_YELLOW}Renaming cancelled by user.{C_RESET}\n")
                    return
            except (KeyboardInterrupt, EOFError):
                print(f"\n{C_YELLOW}Renaming cancelled.{C_RESET}\n")
                return

        # Execute renaming
        success_count = 0
        for item in rename_plan:
            if item["already_named"]:
                continue
            try:
                os.rename(item["old_path"], item["new_path"])
                print(f"  {C_GREEN}✔ Renamed:{C_RESET} {item['old_name']} ➔ {item['new_name']}")
                success_count += 1
            except Exception as e:
                print(f"  {C_RED}❌ Failed to rename {item['old_name']}:{C_RESET} {e}")

        print(f"\n{C_GREEN}Successfully renamed {success_count}/{to_rename_count} invoice files!{C_RESET}\n")

# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------

def interactive_charge_session_loop(analyzer, filtered, allow_back=False):
    """Shared interactive [#]/[u]pdate/[q]uit prompt loop for a charging
    session list. Used both at the top level (all months, no month
    breakdown) and after drilling into a single month from
    display_charging_months_menu() below - allow_back=True adds a [b]ack
    option in the latter case so the user can return to the months table
    instead of quitting the whole tool. Returns True if the user chose to
    go back (only possible when allow_back=True), otherwise None on quit."""
    while True:
        try:
            bad_sessions = [
                s for s in analyzer.reconciled_sessions
                if "MISMATCH" in s.get("status", "") or "WRONG" in s.get("status", "") or
                (s.get("invoice_number") and s.get("invoice_cost") is not None and (
                    abs(s.get("invoice_cost", 0) - s.get("tessie_cost", 0)) >= 0.01 or
                    (s.get("invoice_rate") is not None and abs(s.get("invoice_rate", 0) - s.get("tessie_rate", 0)) >= 0.01)
                ))
            ]
            back_opt = ", [b]ack to months" if allow_back else ""
            if bad_sessions:
                prompt_msg = f"{C_BOLD}Select [#] to inspect, {C_YELLOW}[u]pdate all bad entries from invoices{C_RESET}{C_BOLD}{back_opt}, or [q]uit: {C_RESET}"
            else:
                prompt_msg = f"{C_BOLD}Select [#] to inspect / manually correct{back_opt}, or [q]uit: {C_RESET}"

            choice = input(prompt_msg).strip().lower()
            if not choice or choice in ['q', 'quit', 'exit']:
                return None
            if allow_back and choice in ['b', 'back']:
                return True
            if choice in ['u', 'update', 'fix', 'fix-all', 'sync']:
                analyzer.update_master_from_invoices()
                filtered = [s for s in analyzer.reconciled_sessions if any(s["charge_index"] == orig["charge_index"] for orig in filtered)] or analyzer.reconciled_sessions
                analyzer.print_sessions_table(filtered)
            elif choice.isdigit():
                analyzer.inspect_session(int(choice))
                # Refresh filtered list and reprint table
                filtered = [s for s in analyzer.reconciled_sessions if any(s["charge_index"] == orig["charge_index"] for orig in filtered)] or analyzer.reconciled_sessions
                analyzer.print_sessions_table(filtered)
            else:
                back_hint = "'b' to go back, " if allow_back else ""
                print(f"Enter a session #, 'u' to update all bad entries, {back_hint}or 'q' to quit.")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def display_charging_months_menu(filtered_sessions, analyzer):
    """Months overview for charging sessions - user asked why this tool
    doesn't break results into months the way tessie_drives_analyzer.py's
    default view already does, and wanted a total $ per month alongside
    it (mirroring that tool's total-km-per-month). Charging sessions don't
    need a further days sub-level the way trip legs do (most days have at
    most one session), so this drills straight from a month row into that
    month's normal print_summary()/print_sessions_table() view."""
    months_dict = defaultdict(list)
    for s in filtered_sessions:
        if not s.get("datetime"):
            continue
        months_dict[s["datetime"].strftime("%Y-%m")].append(s)

    sorted_months = sorted(months_dict.keys(), reverse=True)

    while True:
        rows_data = []
        for idx, m_key in enumerate(sorted_months):
            m_sessions = months_dict[m_key]
            dt_m = datetime.strptime(m_key + "-01", "%Y-%m-%d")
            n_disp = sum(s["dispenser_kwh"] for s in m_sessions)
            n_cost = sum((s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]) for s in m_sessions)
            n_avg = (n_cost / n_disp) if n_disp > 0 else 0.0
            rows_data.append({
                "idx": f" [{idx + 1}]",
                "month": f" {dt_m.strftime('%B %Y')}",
                "sessions": f" {len(m_sessions)}",
                "energy": f" {n_disp:,.1f} kWh",
                "spend": f" ${n_cost:,.2f}",
                "avg": f" ${n_avg:.2f}/kWh",
            })

        # The grand-total row (added below) can be wider than any individual
        # month row (e.g. "1,205.7 kWh" vs. "418.2 kWh") - its cells must
        # feed into the column-width calculation too, or it gets truncated.
        gt_disp = sum(s["dispenser_kwh"] for s in filtered_sessions)
        gt_cost = sum((s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]) for s in filtered_sessions)
        gt_avg = (gt_cost / gt_disp) if gt_disp > 0 else 0.0
        total_row = {
            "idx": "",
            "month": " TOTAL",
            "sessions": f" {len(filtered_sessions)}",
            "energy": f" {gt_disp:,.1f} kWh",
            "spend": f" ${gt_cost:,.2f}",
            "avg": f" ${gt_avg:.2f}/kWh",
        }
        width_rows = rows_data + [total_row]

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in width_rows), default=5))
        w_month = max(len(" Month "), max((display_len(r["month"]) + 1 for r in width_rows), default=18))
        w_sessions = max(len(" Sessions "), max((display_len(r["sessions"]) + 1 for r in width_rows), default=11))
        w_energy = max(len(" Energy "), max((display_len(r["energy"]) + 1 for r in width_rows), default=13))
        w_spend = max(len(" Total Spend "), max((display_len(r["spend"]) + 1 for r in width_rows), default=15))
        w_avg = max(len(" Avg $/kWh "), max((display_len(r["avg"]) + 1 for r in width_rows), default=13))

        total_inner = w_idx + w_month + w_sessions + w_energy + w_spend + w_avg + 5
        title = f" 🗓️ CHARGING SESSIONS BY MONTH ({len(filtered_sessions):,} Sessions Across {len(sorted_months)} Months)"
        t_len = display_len(title)
        if t_len + 2 > total_inner:
            w_avg += (t_len + 2 - total_inner)
            total_inner = w_idx + w_month + w_sessions + w_energy + w_spend + w_avg + 5

        border_top = format_box_line("┌", "─", "┐", [total_inner])
        border_top_header = format_box_line("├", "┬", "┤", [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg])
        border_data_divider = format_box_line("├", "┼", "┤", [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg])
        border_bot = format_box_line("└", "┴", "┘", [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg])

        print(f"\n{border_top}")
        print(format_title_line(title, total_inner, "left"))
        print(border_top_header)

        m_widths = [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg]
        m_headers = [" #", " Month", " Sessions", " Energy", " Total Spend", " Avg $/kWh"]
        m_aligns = ["right", "left", "right", "right", "right", "right"]
        print(format_row(m_headers, m_widths))
        print(border_data_divider)

        for r in rows_data:
            m_cells = [r["idx"] + " ", r["month"], r["sessions"] + " ", r["energy"] + " ", r["spend"] + " ", r["avg"] + " "]
            print(format_row(m_cells, m_widths, m_aligns))

        print(border_data_divider)
        gt_cells = [total_row["idx"] + " ", total_row["month"], total_row["sessions"] + " ", total_row["energy"] + " ", total_row["spend"] + " ", total_row["avg"] + " "]
        print(format_row(gt_cells, m_widths, m_aligns))
        print(border_bot)

        try:
            prompt = f"Select Month [1-{len(sorted_months)}], [a]ll months, [q]uit: "
            choice = input(prompt).strip().lower()
            if not choice or choice in ("q", "quit", "exit"):
                return
            elif choice in ("a", "all"):
                analyzer.print_summary(filtered_sessions)
                analyzer.print_sessions_table(filtered_sessions)
                interactive_charge_session_loop(analyzer, filtered_sessions, allow_back=True)
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_months):
                selected_m = sorted_months[int(choice) - 1]
                m_sessions = months_dict[selected_m]
                analyzer.print_summary(m_sessions)
                analyzer.print_sessions_table(m_sessions)
                interactive_charge_session_loop(analyzer, m_sessions, allow_back=True)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            print()
            return


def main():
    parser = argparse.ArgumentParser(
        description="Tessie Charging & Supercharger Reconciliation Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", "-c", help="Path to custom config.json file")
    parser.add_argument("--tessie-dir", "-d", help="Path to Tessie directory containing CSVs and JSONs")
    parser.add_argument("--invoices-dir", "-i", help="Path to directory containing invoice PDFs / CSV receipts")
    
    parser.add_argument("--superchargers", "-s", action="store_true", help="Filter for Tesla Supercharger sessions only")
    parser.add_argument("--third-party", "-t", action="store_true", help="Filter for 3rd-Party Fast/AC charging sessions only")
    parser.add_argument("--home", "-H", action="store_true", help="Filter for Home AC charging sessions only")
    parser.add_argument("--exclude-superchargers", action="store_true", help="Exclude Tesla Supercharger sessions (e.g. combine with other filters to show everything else)")
    parser.add_argument("--exclude-third-party", action="store_true", help="Exclude 3rd-Party Fast/AC charging sessions")
    parser.add_argument("--exclude-home", action="store_true", help="Exclude Home AC charging sessions (e.g. show all non-home sessions)")
    parser.add_argument("--unreconciled", "-u", action="store_true", help="Filter for unreconciled sessions or rate mismatches")
    parser.add_argument("--audit", "-a", action="store_true", help="List mismatches without interactive prompting")
    parser.add_argument("--month", help="Filter sessions by month (e.g. '2026-09', 'september', 'aug')")
    parser.add_argument("--since", help="Filter sessions on or after date (YYYY-MM-DD or relative: today, yesterday, monday)")
    parser.add_argument("--until", help="Filter sessions on or before date (YYYY-MM-DD)")
    
    parser.add_argument("--inspect", help="Deep-dive inspect a specific session by Charge # or Date")
    parser.add_argument("--correlation", "--compare", action="store_true", help="Display 3-way correlation audit comparing Tessie summary, detailed telemetry CSV, and Invoices")
    parser.add_argument("--list-chargers", action="store_true", help="(Deprecated) List registered stations. Prefer using ./Tools/find_chargers.py")
    parser.add_argument("--consolidate", action="store_true", help="Consolidate all charges into charges_master.csv")
    parser.add_argument("--update-master", "--update-all", "--fix-all", "--sync-invoices", action="store_true", help="Update charges_master.csv and individual charge files with official invoice costs/rates")
    parser.add_argument("--rename-invoices", "--rename", action="store_true", help="Rename invoice PDFs to <date/time>_<network>_<invoice_num>_<place>.pdf")
    parser.add_argument("--rename-format", help="Custom renaming template (e.g. '{datetime}_{network}_{invoice_num}_{place}.pdf')")
    parser.add_argument("--dry-run", action="store_true", help="Preview renaming without modifying files on disk")
    parser.add_argument("--yes", "-y", action="store_true", help="Automatically confirm renaming without interactive prompt")
    parser.add_argument("--export", help="Export reconciled results to a CSV or JSON file")
    parser.add_argument("--tolerance-mins", type=int, default=None, help="Invoice matching time tolerance in minutes (default: 45 or config)")

    args = parser.parse_args()

    analyzer = TessieChargingAnalyzer(
        config_path=args.config,
        tessie_dir=args.tessie_dir,
        invoices_dir=args.invoices_dir,
        tolerance_mins=args.tolerance_mins
    )

    if args.list_chargers:
        analyzer.list_chargers()
        return

    if args.consolidate:
        analyzer.consolidate_charges_master()
        return

    if args.update_master:
        analyzer.update_master_from_invoices(auto_confirm=args.yes)
        return

    if args.rename_invoices:
        analyzer.rename_invoices(
            format_pattern=args.rename_format,
            dry_run=args.dry_run,
            auto_confirm=args.yes
        )
        return

    reconciled = analyzer.reconcile(interactive=False)
    if sys.stdin.isatty() and not args.audit:
        analyzer.interactive_discrepancy_menu()
        reconciled = analyzer.reconciled_sessions

    if args.inspect:
        analyzer.inspect_session(args.inspect)
        return

    filtered = reconciled
    if args.superchargers:
        filtered = [s for s in filtered if s["is_supercharger"]]
    elif args.third_party:
        filtered = [s for s in filtered if s["is_fast_charger"] and not s["is_supercharger"]]
    elif args.home:
        filtered = [s for s in filtered if s["emoji"] == "🏠"]

    # Exclude filters apply independently of (and after) the include filters
    # above, so e.g. --exclude-home alone means "show everything except home".
    if args.exclude_superchargers:
        filtered = [s for s in filtered if not s["is_supercharger"]]
    if args.exclude_third_party:
        filtered = [s for s in filtered if not (s["is_fast_charger"] and not s["is_supercharger"])]
    if args.exclude_home:
        filtered = [s for s in filtered if s["emoji"] != "🏠"]

    if args.unreconciled:
        filtered = [s for s in filtered if s["status"] in ["UNRECONCILED ❓", "RATE MISMATCH ⚠️", "COST MISMATCH ⚠️", "TESSIE RATE WRONG ⚠️", "INVOICE ONLY 📄"]]

    if args.month:
        m_target = args.month.strip().lower()

        def _month_match(s):
            if not s.get("datetime"):
                return False
            ym = s["datetime"].strftime("%Y-%m")
            mname = s["datetime"].strftime("%B").lower()
            return m_target in ym or mname.startswith(m_target)

        filtered = [s for s in filtered if _month_match(s)]

    if args.since:
        s_dt = parse_flexible_date(args.since)
        if s_dt:
            filtered = [s for s in filtered if s["datetime"] and s["datetime"] >= s_dt]

    if args.until:
        u_dt = parse_flexible_date(args.until)
        if u_dt:
            u_dt_end = u_dt + timedelta(days=1)
            filtered = [s for s in filtered if s["datetime"] and s["datetime"] <= u_dt_end]

    if args.correlation:
        analyzer.print_correlation_table(filtered)
        if args.export:
            analyzer.export_reconciliation(args.export, filtered)
        return

    # Months overview: mirrors tessie_drives_analyzer.py's default
    # months-menu behavior. Skipped (falls through to the flat summary +
    # table below, exactly as before) whenever the result is already
    # scoped to a single month, --month/--export/--audit were given, or
    # stdin isn't a tty (non-interactive use, e.g. scripted/piped).
    months_present = sorted(set(s["datetime"].strftime("%Y-%m") for s in filtered if s.get("datetime")), reverse=True)
    if sys.stdin.isatty() and not args.export and not args.audit and not args.month and len(months_present) > 1:
        display_charging_months_menu(filtered, analyzer)
        return

    analyzer.print_summary(filtered)
    analyzer.print_sessions_table(filtered)

    if args.export:
        analyzer.export_reconciliation(args.export, filtered)

    # Interactive session selection loop: allows selecting session to inspect or manually correct rate/cost
    if sys.stdin.isatty() and not args.inspect and not args.export and not args.audit:
        interactive_charge_session_loop(analyzer, filtered)


if __name__ == "__main__":
    main()
