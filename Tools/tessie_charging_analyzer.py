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

# Auto re-exec inside local direnv/pyenv virtual environment if not already active
_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_script_dir)
_candidates = []
if "VIRTUAL_ENV" in os.environ:
    _candidates.extend([
        os.path.join(os.environ["VIRTUAL_ENV"], "bin", "python3"),
        os.path.join(os.environ["VIRTUAL_ENV"], "bin", "python")
    ])
import glob as _glob
for _d in _glob.glob(os.path.join(_repo_root, ".direnv", "python*")):
    _candidates.extend([os.path.join(_d, "bin", "python3"), os.path.join(_d, "bin", "python")])
for _d in _glob.glob(os.path.join(_repo_root, ".venv*")):
    _candidates.extend([os.path.join(_d, "bin", "python3"), os.path.join(_d, "bin", "python")])

for _py_candidate in _candidates:
    if os.path.isfile(_py_candidate) and os.path.abspath(sys.executable) != os.path.abspath(_py_candidate):
        try:
            import pypdf
        except ImportError:
            try:
                os.execv(_py_candidate, [_py_candidate] + sys.argv)
            except Exception:
                pass

import re
import csv
import json
import math
import zlib
import shutil
import argparse
import unicodedata
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from collections import defaultdict

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

try:
    import ctypes
    libc = ctypes.CDLL("libc.dylib" if sys.platform == "darwin" else "libc.so.6")
    _libc_wcwidth = libc.wcwidth
    _libc_wcwidth.argtypes = [ctypes.c_wchar]
    _libc_wcwidth.restype = ctypes.c_int

    def char_width(c):
        if c in ('\ufe0f', '\ufe0e'):
            return 0
        w = _libc_wcwidth(c)
        return max(0, w) if w >= 0 else 1
except Exception:
    def char_width(c):
        if c in ('\ufe0f', '\ufe0e'):
            return 0
        if c in ('🔴', '⚡', '🔌', '🏠', '🅿️', '✅', '⚠️', '❌', '❓', '📄', '💾', '📊', '🚗', '🕒', '📍', '💰', '⚙️'):
            return 2
        w = unicodedata.east_asian_width(c)
        if w in ('W', 'F'):
            return 2
        return 1

def display_len(s):
    clean = re.sub(r"\033\[[0-9;]*m", "", s)
    return sum(char_width(c) for c in clean)

def truncate_display(s, max_width, ellipsis="…"):
    if display_len(s) <= max_width:
        return s
    el_w = display_len(ellipsis)
    target = max(1, max_width - el_w)
    
    tokens = re.split(r"(\033\[[0-9;]*m)", s)
    curr = ""
    curr_len = 0
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("\033["):
            curr += tok
            continue
        for c in tok:
            cw = char_width(c)
            if curr_len + cw > target:
                return curr + ellipsis + (C_RESET if "\033[" in s else "")
            curr += c
            curr_len += cw
    return curr + ellipsis + (C_RESET if "\033[" in s else "")

def pad_display(s, target_width, align="left", truncate=False):
    if truncate and display_len(s) > target_width:
        s = truncate_display(s, target_width)
    d_len = display_len(s)
    pad_len = max(0, target_width - d_len)
    if align == "right":
        return " " * pad_len + s
    elif align == "center":
        left = pad_len // 2
        right = pad_len - left
        return " " * left + s + " " * right
    else:
        return s + " " * pad_len

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

def wrap_text_display(s, max_width):
    clean = re.sub(r"\033\[[0-9;]*m", "", s)
    if display_len(clean) <= max_width:
        return [s]
    words = s.split(" ")
    lines = []
    current = ""
    for w in words:
        candidate = f"{current} {w}".strip() if current else w
        if display_len(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines or [s]

# ANSI Colors
C_RESET   = "\033[0m"
C_BOLD    = "\033[1m"
C_DIM     = "\033[2m"
C_RED     = "\033[91m"
C_GREEN   = "\033[92m"
C_YELLOW  = "\033[93m"
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

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(d_clean, fmt)
        except ValueError:
            pass
    return None

def clean_tokens(s):
    return set(re.findall(r"\w+", (s or "").lower()))

def find_mounted_tesla_volumes(subdir=None):
    """
    Dynamically discovers all mounted volumes matching TESLADRIVE* under /Volumes.
    If subdir is provided (e.g., 'TeslaCam', 'Tessie', 'Tools', 'invoices'),
    returns existing subdirectories within those volumes.
    """
    volumes_root = "/Volumes"
    if not os.path.isdir(volumes_root):
        return []
    discovered = []
    seen = set()
    try:
        entries = sorted(os.listdir(volumes_root))
    except Exception:
        entries = []
    for entry in entries:
        if entry.upper().startswith("TESLADRIVE"):
            vol_path = os.path.join(volumes_root, entry)
            if os.path.isdir(vol_path):
                target = os.path.join(vol_path, subdir) if subdir else vol_path
                if os.path.isdir(target):
                    real_p = os.path.abspath(os.path.realpath(target))
                    if real_p not in seen:
                        seen.add(real_p)
                        discovered.append(real_p)
    return discovered

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
        emoji = "🔴⚡"
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
                    emoji = "🔴⚡"
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
        ] + find_mounted_tesla_volumes("Tessie")
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
            ] + find_mounted_tesla_volumes("Tessie/invoices") + find_mounted_tesla_volumes("invoices")
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
        self.charging_stations = self.load_json_registry("tesla_chargers.json") or self.load_json_registry("charging.json") or self.load_json_registry("destination_chargers.json")
        self.charging_archived = self.load_json_registry("tesla_chargers_archived.json") or self.load_json_registry("charging_archived.json") or self.load_json_registry("destination_chargers_archived.json")
        self.places = self.load_json_registry("places.json")
        
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
        ] + [os.path.join(v, "config.json") for v in find_mounted_tesla_volumes("Tessie")]
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
        data = {}
        for td in self.tessie_dirs:
            p = os.path.join(td, filename)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            for k, v in loaded.items():
                                if k not in data:
                                    data[k] = v
                except Exception:
                    pass
        return data

    def resolve_location(self, address, saved_loc="", lat=None, lon=None, is_supercharger=False, is_fast=False):
        addr_clean = (address or "").lower()
        saved_clean = (saved_loc or "").strip()

        # 1. Superchargers Registry
        for sc_name, sc_data in self.superchargers.items():
            meta = sc_data.get("tesla_metadata", {})
            loc = sc_data.get("location", {})
            display_name = sc_name
            kws = meta.get("keywords") or []
            
            if saved_clean and (saved_clean.lower() == sc_name.lower() or any(k.lower() in saved_clean.lower() for k in kws)):
                return (display_name, "Tesla Supercharger", "🔴⚡", sc_data)
            
            sc_lat = loc.get("lat")
            sc_lon = loc.get("lon")
            sc_rad = loc.get("radius_m", 250)
            if lat is not None and lon is not None and sc_lat is not None and sc_lon is not None:
                if haversine_distance_m(lat, lon, sc_lat, sc_lon) <= sc_rad:
                    return (display_name, "Tesla Supercharger", "🔴⚡", sc_data)
            
            for kw in kws:
                if kw.lower() in addr_clean:
                    return (display_name, "Tesla Supercharger", "🔴⚡", sc_data)

        # 2. 3rd-Party & Home Charging Registry
        for st_name, st_data in self.charging_stations.items():
            st_type = st_data.get("type", "ac")
            network = st_data.get("network") or st_data.get("operator") or ("Tesla Wall Connector" if st_type == "home" else "3rd-Party")
            short_name = st_data.get("name") or st_name
            kws = st_data.get("keywords") or []
            st_lat = st_data.get("lat")
            st_lon = st_data.get("lon")
            st_rad = st_data.get("radius_m", 150)
            emoji = "🏠⚡" if st_type == "home" else ("🔌" if st_type == "dc_fast" else "🅿️")

            if saved_clean and (saved_clean.lower() == st_name.lower() or any(k.lower() in saved_clean.lower() for k in kws)):
                return (short_name, network, emoji, st_data)

            if lat is not None and lon is not None and st_lat is not None and st_lon is not None:
                if haversine_distance_m(lat, lon, st_lat, st_lon) <= st_rad:
                    return (short_name, network, emoji, st_data)

            for kw in kws:
                if kw.lower() in addr_clean:
                    return (short_name, network, emoji, st_data)

        # 3. Places Registry
        for p_name, p_data in self.places.items():
            nickname = p_data.get("nickname") or p_name
            kws = p_data.get("keywords") or []
            p_lat = p_data.get("lat")
            p_lon = p_data.get("lon")
            p_rad = p_data.get("radius_m", 150)

            if saved_clean and (saved_clean.lower() == p_name.lower() or any(k.lower() in saved_clean.lower() for k in kws)):
                emoji = "🔴⚡" if is_supercharger else ("🔌" if is_fast else "🅿️")
                net = "Tesla Supercharger" if is_supercharger else ("DC Fast" if is_fast else "Destination AC")
                return (nickname, net, emoji, p_data)

            if lat is not None and lon is not None and p_lat is not None and p_lon is not None:
                if haversine_distance_m(lat, lon, p_lat, p_lon) <= p_rad:
                    emoji = "🔴⚡" if is_supercharger else ("🔌" if is_fast else "🅿️")
                    net = "Tesla Supercharger" if is_supercharger else ("DC Fast" if is_fast else "Destination AC")
                    return (nickname, net, emoji, p_data)

            for kw in kws:
                if kw.lower() in addr_clean:
                    emoji = "🔴⚡" if is_supercharger else ("🔌" if is_fast else "🅿️")
                    net = "Tesla Supercharger" if is_supercharger else ("DC Fast" if is_fast else "Destination AC")
                    return (nickname, net, emoji, p_data)

        display = saved_clean or (address.split(",")[0].strip() if address else "Unknown Location")
        if is_supercharger:
            return (display, "Tesla Supercharger", "🔴⚡", {})
        elif is_fast:
            return (display, "3rd-Party Fast", "🔌", {})
        else:
            return (display, "AC Charger", "🅿️", {})

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
        lookup_name = place_name or (registry_obj.get("tesla_metadata", {}).get("name") if registry_obj else None)
        if lookup_name:
            candidates = self.superchargers_archived.get(lookup_name) or self.charging_archived.get(lookup_name)
            if not candidates:
                for k, v in self.superchargers_archived.items():
                    if k.lower() in lookup_name.lower() or lookup_name.lower() in k.lower():
                        candidates = v
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

        tariffs = target_obj.get("tariffs", {})
        cost_cfg = target_obj.get("tessie_cost_config") or target_obj.get("costs") or {}
        
        if tariffs:
            user_group = "non_tesla" if is_non_tesla else "tesla_members"
            group_cfg = tariffs.get(user_group, {})
            p_model = group_cfg.get("pricing_model", "time_of_use" if tariffs.get("has_tou_pricing") else "flat")
            schedules = group_cfg.get("rate_schedules", [])
            flat_rate = group_cfg.get("rate_per_kwh") or cost_cfg.get("per_kwh_flat") or cost_cfg.get("flat_per_kwh")
        else:
            p_model = cost_cfg.get("pricing_model", "flat")
            schedules = cost_cfg.get("rate_schedules", [])
            flat_rate = cost_cfg.get("per_kwh_flat") or cost_cfg.get("flat_per_kwh")

        if p_model == "flat" or not schedules:
            return {
                "rate_per_kwh": flat_rate or 0.0,
                "schedule_name": "Flat Rate",
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
                    break

        if matched_rate is None:
            matched_rate = flat_rate
            matched_sched_name = "Standard Rate"

        return {
            "rate_per_kwh": matched_rate,
            "schedule_name": matched_sched_name,
            "is_archived": is_archived,
            "timezone": tz_name
        }

    def auto_ingest_from_landing(self):
        landing = self.landing_dir
        if not landing or not os.path.isdir(landing):
            return
        
        archive_dir = os.path.join(self.repo_root, "Tessie", "archive")
        os.makedirs(archive_dir, exist_ok=True)
        
        # Collect existing known charge timestamps from all existing files
        existing_charge_timestamps = set()
        for td in self.tessie_dirs:
            if not os.path.isdir(td):
                continue
            try:
                fnames = os.listdir(td)
            except Exception:
                continue
            for fname in fnames:
                if (fname == "charges_master.csv" or "charges_summary" in fname or "-charges.csv" in fname) and not "telemetry" in fname:
                    try:
                        with open(os.path.join(td, fname), "r", encoding="utf-8", errors="ignore") as f_ex:
                            r_ex = csv.DictReader(f_ex)
                            scol = next((c for c in (r_ex.fieldnames or []) if c and c.startswith("Started At")), None)
                            if scol:
                                for row in r_ex:
                                    val = row.get(scol, "").strip()
                                    if val:
                                        existing_charge_timestamps.add(val)
                    except Exception:
                        pass

        moved = 0
        try:
            landing_files = os.listdir(landing)
        except Exception:
            landing_files = []

        for f in landing_files:
            if not f.endswith(".csv"):
                continue
            
            fp = os.path.join(landing, f)
            try:
                with open(fp, "r", encoding="utf-8-sig", errors="ignore") as csv_f:
                    reader = csv.reader(csv_f)
                    header = next(reader, None)
                    if not header:
                        continue
                    hset = set(h.strip() for h in header)
                    
                    is_charge_csv = ("Location" in hset and "Energy Added (kWh)" in hset)
                    is_tessie = ("Starting Location" in hset and "Distance (km)" in hset) or \
                                is_charge_csv or \
                                ("Speed (km/h)" in hset or "Speed (mph)" in hset) or \
                                ("Charger Power (kW)" in hset or "Charger Voltage (V)" in hset)
                    
                    if not is_tessie:
                        continue

                    # If this is a charges CSV, check if all sessions in it are already known
                    if is_charge_csv and existing_charge_timestamps:
                        csv_f.seek(0)
                        dict_r = csv.DictReader(csv_f)
                        scol = next((c for c in (dict_r.fieldnames or []) if c and c.startswith("Started At")), None)
                        if scol:
                            file_timestamps = [r.get(scol, "").strip() for r in dict_r if r.get(scol, "").strip()]
                            if file_timestamps and all(t in existing_charge_timestamps for t in file_timestamps):
                                print(f"\033[93m⚠️  [Landing] Rejected '{f}': All {len(file_timestamps)} charging session(s) are for already known dates ({file_timestamps[0][:10]} to {file_timestamps[-1][:10]}). Invoice-locked data protected.\033[0m")
                                continue

                    ts = datetime.now().strftime("%Y%m%d%H%M")
                    dst_name = f"{f}.{ts}"
                    shutil.move(fp, os.path.join(archive_dir, dst_name))
                    print(f"\033[94m📥 Ingested & Archived:\033[0m {dst_name}")
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
        for td in self.tessie_dirs:
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
        
        for td in self.tessie_dirs:
            if not os.path.isdir(td):
                continue
            
            # Prioritize charges_master.csv first so invoice-locked records take precedence
            candidates = []
            master_file = os.path.join(td, "charges_master.csv")
            if os.path.isfile(master_file):
                candidates.append(master_file)
            try:
                fnames = sorted(os.listdir(td))
            except Exception:
                continue
            for fname in fnames:
                fp = os.path.join(td, fname)
                if fp != master_file and ("charges_summary" in fname or "-charges.csv" in fname) and not "telemetry" in fname:
                    candidates.append(fp)

            for fpath in candidates:
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        reader = csv.DictReader(f)
                        started_col = next((col for col in (reader.fieldnames or []) if col and col.startswith("Started At")), "Started At")
                        for row in reader:
                            s_at = row.get(started_col)
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
            try:
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
            except Exception:
                pass
        
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
                charger_energies_kwh = 0.0
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
                            charger_energies_kwh += 0.5 * (p0 + p1) * dt_hours
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
                    "integrated_charger_kwh": charger_energies_kwh,
                    "integrated_pack_kwh": pack_energies_kwh
                }
        except Exception:
            return None

    def load_detailed_charges(self):
        detailed = []
        seen_paths = set()
        search_dirs = [self.landing_dir] + self.tessie_dirs
        for td in list(self.tessie_dirs):
            for sub in ["charges", "telemetry", "archive"]:
                sub_p = os.path.join(td, sub)
                if os.path.isdir(sub_p):
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
        print(f"\033[91m ⚠️  {len(self.discrepancies)} CHARGE TARIFF/COST DISCREPANCIES DETECTED (Net/Gross CSV issue)\033[0m")
        print(f"\033[90m Note: Only 'Cost Per kWh' and 'Cost' are patched. Energy (kWh) is strictly preserved.\033[0m")
        print(f"\033[91m==========================================================================\033[0m")
        
        for i, d in enumerate(self.discrepancies):
            s_at = d.get("started_at_str")
            place = d.get("place_name")
            bat_kwh = d.get("energy_added_kwh", 0)
            t_cost = d.get("cost", 0)
            t_rate = d.get("cost_per_kwh")
            if t_rate is None or t_rate == 0:
                t_rate = (t_cost / bat_kwh) if bat_kwh > 0 else 0.0
            
            disc = d.get("_discrepancy", {})
            i_cost = disc.get("invoice_cost", 0)
            i_rate = disc.get("invoice_rate", 0)
            
            print(f"\n\033[91m[{i+1}] {s_at} @ {place}\033[0m")
            print(f"    Current CSV:       Rate: ${t_rate:.2f}/kWh  |  Total Cost: ${t_cost:.2f}")
            print(f"    Invoice (Gross):   Rate: ${i_rate:.2f}/kWh  |  Total Cost: ${i_cost:.2f}")
            print(f"    Car Telemetry:     {bat_kwh:.2f} kWh added (Kept intact - not modified)")
            
        print(f"\nOptions: [1-{len(self.discrepancies)}] to update rate/cost, [a]ll to update all, [s]kip/continue")
        
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
                
                # Fix the chosen discrepancies across charges_master and source CSVs
                fixed_count = 0
                for c in to_fix:
                    disc = c.get("_discrepancy", {})
                    i_cost = disc.get("invoice_cost")
                    i_rate = disc.get("invoice_rate")
                    s_at = c.get("started_at_str", "")
                    loc = c.get("location_raw", "")
                    
                    if i_cost is not None and i_rate is not None:
                        self.patch_charge_record(s_at, loc, i_cost, i_rate)
                        c["cost"] = float(i_cost)
                        c["cost_per_kwh"] = float(i_rate)
                        c["status"] = "MATCHED ✅"
                        if c in self.discrepancies:
                            self.discrepancies.remove(c)
                        fixed_count += 1
                
                if fixed_count > 0:
                    print(f"✅ Updated rate and gross cost for {fixed_count} charge(s). Energy telemetry kept intact.")
                    self.load_charges()
                    self.reconcile(interactive=False)

                if not self.discrepancies:
                    break
                else:
                    return self.interactive_discrepancy_menu()

                    
            except (KeyboardInterrupt, EOFError):
                break

    def reconcile(self, interactive=False):
        if not self._loaded:
            self.load_charges()
            self.load_invoices()
            self.load_detailed_charges()
            self._loaded = True

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
            matched_detailed = None
            for dt_rec in self.detailed_charges:
                if dt_rec.get("start_datetime") and dt:
                    time_diff = abs((dt_rec["start_datetime"] - dt).total_seconds())
                    if time_diff <= 900:
                        matched_detailed = dt_rec
                        break
                    if charge.get("ended_at") and (charge["started_at"] <= dt_rec["start_datetime"] <= charge["ended_at"]):
                        matched_detailed = dt_rec
                        break

            tessie_bat_kwh = charge["energy_added_kwh"]
            tessie_car_kwh = charge["energy_used_kwh"]
            invoice_disp_kwh = None

            dispenser_kwh = tessie_car_kwh if tessie_car_kwh > 0 else tessie_bat_kwh
            battery_kwh = tessie_bat_kwh
            tessie_cost = charge["cost"]
            invoice_cost = None
            invoice_rate = None
            inv_num = None
            status = "HOME / AC 🏠" if charge["emoji"] == "🏠⚡" else ("UNRECONCILED ❓" if (charge["is_supercharger"] or charge["is_fast_charger"]) else "AC UNBILLED 🅿️")

            if best_inv:
                matched_invoice_indices.add(best_inv_idx)
                inv_num = best_inv.get("invoice_number")
                if best_inv.get("energy_kwh"):
                    invoice_disp_kwh = best_inv["energy_kwh"]
                    dispenser_kwh = best_inv["energy_kwh"]
                if best_inv.get("total_cost") is not None:
                    invoice_cost = best_inv["total_cost"]
                if best_inv.get("unit_rate") is not None:
                    invoice_rate = best_inv["unit_rate"]

                cost_diff = abs((invoice_cost or 0) - tessie_cost)
                if cost_diff >= 0.50:
                    status = "TESSIE RATE WRONG ⚠️"
                    charge["_discrepancy"] = {
                        "invoice_cost": invoice_cost,
                        "invoice_rate": invoice_rate,
                        "invoice_kwh": invoice_disp_kwh
                    }
                    self.discrepancies.append(charge)
                else:
                    status = "MATCHED ✅"

            if invoice_disp_kwh and invoice_disp_kwh > 0:
                car_inlet = tessie_car_kwh if tessie_car_kwh > 0 else battery_kwh
                cable_loss_kwh = max(0.0, invoice_disp_kwh - car_inlet)
                car_loss_kwh = max(0.0, car_inlet - battery_kwh)
                loss_kwh = max(0.0, invoice_disp_kwh - battery_kwh)
                efficiency_pct = (battery_kwh / invoice_disp_kwh * 100.0)
            else:
                cable_loss_kwh = 0.0
                car_loss_kwh = max(0.0, tessie_car_kwh - battery_kwh) if tessie_car_kwh > 0 else 0.0
                loss_kwh = car_loss_kwh
                efficiency_pct = (battery_kwh / dispenser_kwh * 100.0) if dispenser_kwh > 0 else 100.0
            
            exp_info = self.get_expected_tariff_rate(charge["registry_obj"], dt, place_name=charge["place_name"])
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
                "charge_index": c_idx + 1,
                "datetime": dt,
                "datetime_str": charge["started_at_str"],
                "duration_mins": charge["duration_mins"],
                "place_name": charge["place_name"],
                "network": charge["network"],
                "emoji": charge["emoji"],
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
                "car_loss_kwh": car_loss_kwh,
                "total_loss_kwh": loss_kwh,
                "dispenser_kwh": dispenser_kwh,
                "battery_kwh": battery_kwh,
                "loss_kwh": loss_kwh,
                "efficiency_pct": efficiency_pct,
                "tessie_cost": tessie_cost,
                "tessie_rate": charge["cost_per_kwh"],
                "invoice_cost": invoice_cost,
                "invoice_rate": invoice_rate,
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
                    "place_name": inv.get("location_raw") or "Unknown Station",
                    "network": inv.get("network", "3rd-Party"),
                    "emoji": inv.get("emoji", "🔌"),
                    "is_supercharger": (inv.get("network") == "Tesla Supercharger"),
                    "is_fast_charger": True,
                    "start_soc": 0,
                    "end_soc": 0,
                    "range_added_km": 0.0,
                    "odometer_km": 0.0,
                    "dispenser_kwh": inv.get("energy_kwh") or 0.0,
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
        self.reconciled_sessions = reconciled
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
        external_tessie = find_mounted_tesla_volumes("Tessie")
        dest_dir = output_dir or (
            external_tessie[0] if external_tessie
            else self.tessie_dirs[0] if self.tessie_dirs else "."
        )
        os.makedirs(dest_dir, exist_ok=True)
        master_file = os.path.join(dest_dir, "charges_master.csv")

        existing_records = []
        existing_keys = set()
        
        fieldnames = [
            "Started At", "Ended At", "Duration (Minutes)", "Location", "Saved Location",
            "Latitude", "Longitude", "Supercharger", "Fast Charger", "Odometer (km)",
            "Energy Added (kWh)", "Energy Used (kWh)", "Rated Range Added (km)", "Starting Battery (%)",
            "Ending Battery (%)", "Cost", "Cost Per kWh"
        ]
        
        import csv
        if os.path.exists(master_file):
            try:
                with open(master_file, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    # Dynamically detect Started At column to handle variable timezones
                    s_at_key = next((k for k in reader.fieldnames if k and k.startswith("Started At")), "Started At")
                    e_at_key = next((k for k in reader.fieldnames if k and k.startswith("Ended At")), "Ended At")
                    
                    # Update fieldnames to match the existing file's timezone formatting if it has one
                    fieldnames[0] = s_at_key
                    fieldnames[1] = e_at_key
                    
                    for row in reader:
                        s_at = row.get(s_at_key, "").strip()
                        loc = row.get("Location", "").strip()
                        added = row.get("Energy Added (kWh)", "").strip()
                        key = (s_at, loc, added)
                        existing_keys.add(key)
                        existing_records.append(row)
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

        with open(master_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_records)

        print(f"\033[92mSuccessfully appended {len(new_records)} new charges (Total: {len(all_records)}) to:\033[0m {master_file}")

    def print_summary(self, filtered_sessions=None):
        sessions = filtered_sessions if filtered_sessions is not None else self.reconciled_sessions
        if not sessions:
            print(f"{C_YELLOW}No charging sessions matching the selected criteria.{C_RESET}")
            return

        total_sessions = len(sessions)
        sc_sessions = [s for s in sessions if s["is_supercharger"]]
        fast_sessions = [s for s in sessions if s["is_fast_charger"] and not s["is_supercharger"]]
        home_sessions = [s for s in sessions if s["emoji"] == "🏠⚡"]
        ac_dest_sessions = [s for s in sessions if not s["is_supercharger"] and not s["is_fast_charger"] and s["emoji"] != "🏠⚡"]

        total_dispenser_kwh = sum(s["dispenser_kwh"] for s in sessions)
        total_battery_kwh = sum(s["battery_kwh"] for s in sessions)
        total_loss_kwh = max(0.0, total_dispenser_kwh - total_battery_kwh)
        overall_eff_pct = (total_battery_kwh / total_dispenser_kwh * 100.0) if total_dispenser_kwh > 0 else 100.0
        
        total_spend = sum((s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]) for s in sessions)
        avg_cost_kwh = (total_spend / total_dispenser_kwh) if total_dispenser_kwh > 0 else 0.0

        total_km_added = sum(s["range_added_km"] for s in sessions)
        petrol_equiv_spend = (total_km_added / 100.0) * 9.5 * 1.95 if total_km_added > 0 else (total_battery_kwh / 0.15 / 100.0) * 9.5 * 1.95
        savings = max(0.0, petrol_equiv_spend - total_spend)

        reconciled_fast = sum(1 for s in sessions if (s["is_supercharger"] or s["is_fast_charger"]) and s["status"] in ["MATCHED ✅", "RATE MISMATCH ⚠️"])
        total_fast_count = len(sc_sessions) + len(fast_sessions)

        box_w = 95
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

        fast_status_color = C_GREEN if (reconciled_fast == total_fast_count and total_fast_count > 0) else C_YELLOW
        inv_src_info = f"({len(self.invoices)} Invoices Loaded from {len(self.invoice_dirs)} directories)"
        kpi_l6 = f"  {C_BOLD}Invoice Reconciliation:{C_RESET} {fast_status_color}{reconciled_fast}/{total_fast_count} Fast Sessions Reconciled{C_RESET} {inv_src_info}"
        print(f"│{pad_display(kpi_l6, box_w - 2)}│")

        if self.config_file:
            cfg_disp = shorten_display_path(self.config_file, 35)
            inv_dir_val = self.config.get("invoices_directory") or (self.invoice_dirs[0] if self.invoice_dirs else "None")
            inv_disp = shorten_display_path(inv_dir_val, 35)
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
            
            type_label = "DC Fast" if net_sessions[0]["is_fast_charger"] else ("Home AC" if emoji == "🏠⚡" else "AC Public")
            name_str = f"{emoji} {net_name}"

            row_str = "│" + "│".join([
                pad_display(f" {name_str}", widths[0], "left", truncate=True),
                pad_display(type_label, widths[1], "center", truncate=True),
                pad_display(str(len(net_sessions)), widths[2], "center", truncate=True),
                pad_display(f"{n_disp:,.1f} kWh ", widths[3], "right", truncate=True),
                pad_display(f"{n_bat:,.1f} kWh ", widths[4], "right", truncate=True),
                pad_display(f"{n_eff:.1f}%", widths[5], "center", truncate=True),
                pad_display(f"${n_cost:,.2f} ", widths[6], "right", truncate=True),
                pad_display(f"${n_avg_rate:.3f} ", widths[7], "right", truncate=True)
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
        for s in sessions:
            clean_place = s["place_name"]
            if s.get("network"):
                clean_place = re.sub(rf"\s*\({re.escape(s['network'])}\)", "", clean_place, flags=re.IGNORECASE)
            p_len = display_len(f" {s['emoji']} {clean_place}") + 1
            if p_len > max_place_w:
                max_place_w = p_len

        place_col_width = max(25, max_place_w)

        headers = [
            "#", "Date / Time", "Place / Station", "Network", "SoC %", "Dur", "Disp kWh", "Bat kWh", "Eff %", "Rate", "Cost", "Invoice", "Status"
        ]
        widths = [
            4, 18, place_col_width, 22, 9, 6, 10, 9, 8, 9, 8, 16, 18
        ]
        total_inner_w = sum(widths) + len(widths) - 1

        print(f"┌{'─' * total_inner_w}┐")
        sess_title = f" ⚡ {C_BOLD}RECONCILED CHARGING SESSIONS ({len(sessions)} sessions){C_RESET}"
        print(f"│{pad_display(sess_title, total_inner_w, 'left', truncate=True)}│")
        top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
        print(top_b)
        
        h_row = "│" + "│".join(
            pad_display(f" {C_BOLD}{h}{C_RESET}" if h in ["Invoice", "Place / Station", "Network", "Status"] else f"{C_BOLD}{h}{C_RESET}", w, "left" if h in ["Place / Station", "Network", "Status", "Invoice"] else ("right" if h in ["Disp kWh", "Bat kWh", "Rate", "Cost"] else "center"))
            for h, w in zip(headers, widths)
        ) + "│"
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
            eff_str = f"{s['efficiency_pct']:.1f}%"
            
            effective_rate = s["invoice_rate"] if s["invoice_rate"] is not None else s["tessie_rate"]
            rate_str = f"${effective_rate:.2f}" if effective_rate else "-"

            cost_val = s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]
            cost_str = f"${cost_val:.2f}" if cost_val is not None else "$0.00"

            inv_str = s["invoice_number"] or "-"
            if len(inv_str) > 13:
                inv_str = inv_str[:12] + "…"

            stat = s["status"]
            if "MATCHED" in stat or "VERIFIED" in stat:
                stat_styled = f"{C_GREEN}{stat}{C_RESET}"
            elif "TESSIE RATE WRONG" in stat or "UNRECONCILED" in stat or "RATE MISMATCH" in stat:
                stat_styled = f"{C_RED}{stat}{C_RESET}"
            elif "INVOICE ONLY" in stat:
                stat_styled = f"{C_BLUE}{stat}{C_RESET}"
            else:
                stat_styled = f"{C_DIM}{stat}{C_RESET}"

            row_str = "│" + "│".join([
                pad_display(idx_str, widths[0], "center", truncate=True),
                pad_display(dt_str, widths[1], "center", truncate=True),
                pad_display(f" {place_str}", widths[2], "left", truncate=True),
                pad_display(f" {net_str}", widths[3], "left", truncate=True),
                pad_display(soc_str, widths[4], "center", truncate=True),
                pad_display(dur_str, widths[5], "center", truncate=True),
                pad_display(f"{disp_str} ", widths[6], "right", truncate=True),
                pad_display(f"{bat_str} ", widths[7], "right", truncate=True),
                pad_display(eff_str, widths[8], "center", truncate=True),
                pad_display(f"{rate_str} ", widths[9], "right", truncate=True),
                pad_display(f"{cost_str} ", widths[10], "right", truncate=True),
                pad_display(f" {inv_str}", widths[11], "left", truncate=True),
                pad_display(f" {stat_styled}", widths[12], "left", truncate=True)
            ]) + "│"
            print(row_str)

        bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
        print(bot_b)
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

            cable_loss = s.get("cable_loss_kwh", 0.0)
            car_loss = s.get("car_loss_kwh", 0.0)
            t_loss = s.get("total_loss_kwh", s["loss_kwh"])

            cable_str = f"{cable_loss:.2f} kWh" if inv_kwh else "-"
            car_str_loss = f"{car_loss:.2f} kWh" if car_kwh > 0 else "-"
            total_loss_str = f"{t_loss:.2f} kWh"
            eff_str = f"{s['efficiency_pct']:.1f}%"

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
            if car_kwh > 0:
                tot_car += car_kwh
            if inv_kwh:
                tot_inv += inv_kwh
                tot_cable_loss += cable_loss
                tot_car_loss += car_loss
                tot_loss += t_loss

            row_str = "│" + "│".join([
                pad_display(idx_str, widths[0], "center", truncate=True),
                pad_display(dt_str, widths[1], "center", truncate=True),
                pad_display(f" {place_str}", widths[2], "left", truncate=True),
                pad_display(f"{bat_str} ", widths[3], "right", truncate=True),
                pad_display(f"{car_str} ", widths[4], "right", truncate=True),
                pad_display(f"{inv_str} ", widths[5], "right", truncate=True),
                pad_display(f"{cable_str} ", widths[6], "right", truncate=True),
                pad_display(f"{car_str_loss} ", widths[7], "right", truncate=True),
                pad_display(f"{total_loss_str} ", widths[8], "right", truncate=True),
                pad_display(eff_str, widths[9], "center", truncate=True),
                pad_display(f"{cost_v} ", widths[10], "right", truncate=True),
                pad_display(telem_str, widths[11], "center", truncate=True)
            ]) + "│"
            print(row_str)

        bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
        print(bot_b)

        if tot_inv > 0:
            box_w = 95
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
        box_w = 95
        idx_label = f"#{s['charge_index']}" if s["charge_index"] is not None else "-"
        header_title = f" ⚡ {C_BOLD}DEEP-DIVE CHARGING INSPECTION: {idx_label} {s['place_name']}{C_RESET}"
        print()
        print(f"┌{'─' * (box_w - 2)}┐")
        print(f"│{pad_display(header_title, box_w - 2, 'left', truncate=True)}│")
        print(f"├{'─' * (box_w - 2)}┤")
        
        l1 = f"  {C_BOLD}Location / Station:{C_RESET}   {s['emoji']} {s['place_name']} ({s['network']})"
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

        if s.get("invoice_disp_kwh"):
            inv_net = (s.get("matched_invoice", {}) or {}).get("network") or s.get("network") or "Dispenser"
            l6a = f"    • {C_BOLD}1. Invoice Meter (Dispenser):{C_RESET}   {s['invoice_disp_kwh']:.2f} kWh ({inv_net} Billed Dispenser Meter)"
            print(f"│{pad_display(l6a, box_w - 2, truncate=True)}│")
        
        car_in = s.get("tessie_car_kwh", 0.0)
        if car_in > 0:
            l6b = f"    • {C_BOLD}2. Vehicle Gross Intake:{C_RESET}       {car_in:.2f} kWh (Electricity Consumed by Car)"
            print(f"│{pad_display(l6b, box_w - 2, truncate=True)}│")

        l7 = f"    • {C_BOLD}3. Net Battery Storage (BMS):{C_RESET}   {s['battery_kwh']:.2f} kWh (Net Battery Pack Chemical Storage)"
        print(f"│{pad_display(l7, box_w - 2, truncate=True)}│")

        if s.get("invoice_disp_kwh") and s.get("cable_loss_kwh") is not None:
            l8a = f"    • {C_BOLD}Dispenser & Cable Loss:{C_RESET}        {s['cable_loss_kwh']:.2f} kWh (Stall electronics & cable resistance)"
            print(f"│{pad_display(l8a, box_w - 2, truncate=True)}│")
            l8b = f"    • {C_BOLD}Vehicle Conditioning Loss:{C_RESET}     {s['car_loss_kwh']:.2f} kWh (BMS, chiller pumps & heat dissipation)"
            print(f"│{pad_display(l8b, box_w - 2, truncate=True)}│")

        eff_color = C_GREEN if s["efficiency_pct"] >= 85.0 else (C_YELLOW if s["efficiency_pct"] >= 75.0 else C_RED)
        l8 = f"    • {C_BOLD}Total Charging Loss:{C_RESET}           {s['loss_kwh']:.2f} kWh  ({eff_color}{s['efficiency_pct']:.1f}% Dispenser-to-Battery{C_RESET})"
        print(f"│{pad_display(l8, box_w - 2, truncate=True)}│")

        # Detailed high-frequency telemetry section if available
        dt_rec = s.get("detailed_telemetry")
        if dt_rec:
            print(f"├{'─' * (box_w - 2)}┤")
            short_src = shorten_display_path(dt_rec['source_file'], 50)
            t_title = f"  {C_BOLD}{C_CYAN}🔋 HIGH-FREQUENCY TELEMETRY AUDIT ({short_src}):{C_RESET}"
            print(f"│{pad_display(t_title, box_w - 2, truncate=True)}│")
            
            t1 = f"    • {C_BOLD}High-Res Telemetry Samples:{C_RESET} {dt_rec['samples_count']} readings over {dt_rec['duration_mins']:.1f} mins"
            print(f"│{pad_display(t1, box_w - 2, truncate=True)}│")
            
            t2 = f"    • {C_BOLD}Charging Power Profile:{C_RESET}     Peak: {dt_rec['peak_power_kw']:.1f} kW  │  Average: {dt_rec['avg_power_kw']:.1f} kW"
            print(f"│{pad_display(t2, box_w - 2, truncate=True)}│")
            
            if dt_rec.get("initial_batt_temp_c") is not None and dt_rec.get("final_batt_temp_c") is not None:
                t3 = f"    • {C_BOLD}Battery Module Temp:{C_RESET}        {dt_rec['initial_batt_temp_c']:.1f}°C ➔ {dt_rec['final_batt_temp_c']:.1f}°C (Peak: {dt_rec['max_batt_temp_c']:.1f}°C, ΔT: +{dt_rec['temp_rise_c']:.1f}°C)"
                print(f"│{pad_display(t3, box_w - 2, truncate=True)}│")
                
            heater_str = "ACTIVE (Preheating)" if dt_rec.get("battery_heater") else "Off (Active cooling loop)"
            amb_str = f"{dt_rec['outside_temp_c']:.1f}°C" if dt_rec.get("outside_temp_c") is not None else "-"
            t4 = f"    • {C_BOLD}Thermal Management:{C_RESET}         Heater: {heater_str}  │  Ambient Temp: {amb_str}"
            print(f"│{pad_display(t4, box_w - 2, truncate=True)}│")
            
            if dt_rec.get("delta_energy_remaining_kwh") is not None:
                t5 = f"    • {C_BOLD}Energy Remaining Delta:{C_RESET}     {dt_rec['energy_remaining_start']:.2f} kWh ➔ {dt_rec['energy_remaining_end']:.2f} kWh (+{dt_rec['delta_energy_remaining_kwh']:.2f} kWh)"
                print(f"│{pad_display(t5, box_w - 2, truncate=True)}│")
                
            if dt_rec.get("integrated_charger_kwh", 0) > 0:
                t6 = f"    • {C_BOLD}Integrated Telemetry Energy:{C_RESET} Port: {dt_rec['integrated_charger_kwh']:.2f} kWh  │  Battery Pack (V×I): {dt_rec['integrated_pack_kwh']:.2f} kWh"
                print(f"│{pad_display(t6, box_w - 2, truncate=True)}│")

        print(f"├{'─' * (box_w - 2)}┤")
        
        c_title = f"  {C_BOLD}{C_GREEN}💰 FINANCIAL & TARIFF AUDIT:{C_RESET}"
        print(f"│{pad_display(c_title, box_w - 2, truncate=True)}│")

        l9 = f"    • {C_BOLD}Tessie Logged Cost:{C_RESET}        ${s['tessie_cost']:.2f} AUD (@ ${s['tessie_rate']:.2f}/kWh)"
        print(f"│{pad_display(l9, box_w - 2, truncate=True)}│")

        if s["invoice_number"]:
            matched_inv = s.get("matched_invoice") if isinstance(s.get("matched_invoice"), dict) else {}
            inv_net_name = matched_inv.get("network") or s["network"]
            inv_type_label = "Tesla Tax Invoice" if (s["is_supercharger"] or "tesla" in inv_net_name.lower()) else f"{inv_net_name} Receipt"
            l10 = f"    • {C_BOLD}{inv_type_label}:{C_RESET}         ${s['invoice_cost']:.2f} AUD (Inv #{s['invoice_number']} @ ${s['invoice_rate']:.2f}/kWh)"
            print(f"│{pad_display(l10, box_w - 2, truncate=True)}│")
            
            delta_cost = (s["invoice_cost"] or 0) - s["tessie_cost"]
            d_color = C_GREEN if abs(delta_cost) < 0.10 else (C_YELLOW if abs(delta_cost) < 1.0 else C_RED)
            l11 = f"    • {C_BOLD}Cost Reconciliation Delta:{C_RESET} {d_color}${delta_cost:+.2f} AUD{C_RESET}"
            print(f"│{pad_display(l11, box_w - 2, truncate=True)}│")
        else:
            l10 = f"    • {C_BOLD}Tax Invoice / Receipt:{C_RESET}     {C_RED}No matching invoice file found in configured invoices directory{C_RESET}"
            print(f"│{pad_display(l10, box_w - 2, truncate=True)}│")

        if s.get("expected_rate") is not None:
            arch_tag = f" {C_MAGENTA}[Historical Archive]{C_RESET}" if s.get("is_archived_tariff") else ""
            sched_label = f" [{s.get('expected_schedule_name')}]" if s.get("expected_schedule_name") else ""
            tz_label = f" (TZ: {s.get('timezone', 'Australia/Sydney')})"
            l12 = f"    • {C_BOLD}Expected Tariff Rate:{C_RESET}      ${s['expected_rate']:.2f}/kWh{sched_label}{tz_label}{arch_tag}"
            print(f"│{pad_display(l12, box_w - 2, truncate=True)}│")

            if s.get("theoretical_cost") is not None:
                th_gst_str = f" (incl. ${s['theoretical_gst']:.2f} GST [10%])" if s.get("theoretical_gst") is not None else ""
                l12b = f"    • {C_BOLD}Theoretical Tariff Cost:{C_RESET}   ${s['theoretical_cost']:.2f} AUD{th_gst_str}"
                print(f"│{pad_display(l12b, box_w - 2, truncate=True)}│")

        stat_color = C_GREEN if ("MATCHED" in s['status'] or "VERIFIED" in s['status']) else C_RED
        l13 = f"    • {C_BOLD}Reconciliation Status:{C_RESET}     {stat_color}{s['status']}{C_RESET}"
        print(f"│{pad_display(l13, box_w - 2, truncate=True)}│")
        print(f"└{'─' * (box_w - 2)}┘\n")

        if sys.stdin.isatty():
            try:
                act = input(f"{C_BOLD}Action:{C_RESET} [m]anually correct rate & cost, [Enter] to return: ").strip().lower()
                if act in ['m', 'manual', 'e', 'edit']:
                    self.prompt_manual_correction(s)
            except (KeyboardInterrupt, EOFError):
                print()

    def prompt_manual_correction(self, s):
        """Allows interactive manual correction of Cost Per kWh and Cost when no invoice is present."""
        print(f"\n\033[93m────────────────────────────────────────────────────────────────────────\033[0m")
        print(f"\033[93m✏️  MANUALLY CORRECT CHARGING SESSION #{s['charge_index']}\033[0m")
        print(f"   Station:              {s['emoji']} {s['place_name']} ({s['datetime_str']})")
        print(f"   Battery Energy Added: {s['battery_kwh']:.2f} kWh (Preserved telemetry)")
        print(f"   Current Telemetry:    Rate: ${s['tessie_rate']:.2f}/kWh | Cost: ${s['tessie_cost']:.2f}")
        print(f"\033[93m────────────────────────────────────────────────────────────────────────\033[0m")
        
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
                self.load_charges()
                self.reconcile(interactive=False)
                
                # Re-inspect to display updated card
                self.inspect_session(s["charge_index"])
        except (ValueError, KeyboardInterrupt, EOFError):
            print("\nCorrection cancelled.")

    def list_chargers(self):
        sc_list = list(self.superchargers.items())
        widths = [4, 7, 28, 28, 8, 6, 12, 28]
        total_w = sum(widths) + len(widths) - 1

        print(f"\n┌{'─' * total_w}┐")
        sc_title = f" 🔴⚡ {C_BOLD}REGISTERED TESLA SUPERCHARGERS ({len(sc_list)} stations){C_RESET}"
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
            stalls_str = f"{hw.get('stalls')} bays" if hw.get("stalls") else "-"
            tier_str = hw.get("tier", "-") or "-"

            row_cells = [
                pad_display(str(idx), widths[0], "center"),
                pad_display(st_state, widths[1], "center"),
                pad_display(f" {name}", widths[2], "left", truncate=True),
                pad_display(f" {suburb}", widths[3], "left", truncate=True),
                pad_display(stalls_str, widths[4], "center"),
                pad_display(tier_str, widths[5], "center"),
                pad_display(access_str, widths[6], "center"),
                pad_display(f" {sched_str}", widths[7], "left", truncate=True)
            ]
            print("│" + "│".join(row_cells) + "│")
        print("└" + "┴".join("─" * w for w in widths) + "┘\n")

        oth_list = list(self.charging_stations.items())
        widths_oth = [4, 7, 28, 22, 10, 10, 12, 28]
        total_w_oth = sum(widths_oth) + len(widths_oth) - 1

        print(f"┌{'─' * total_w_oth}┐")
        oth_title = f" 🔌 {C_BOLD}REGISTERED 3RD-PARTY & HOME CHARGERS ({len(oth_list)} stations){C_RESET}"
        print(f"│{pad_display(oth_title, total_w_oth, 'left')}│")
        print("├" + "┬".join("─" * w for w in widths_oth) + "┤")
        headers_oth = ["#", "State", "Station / Location Name", "Network / Operator", "Type", "Power", "Hardware", "Rates (AUD)"]
        print("│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers_oth, widths_oth)) + "│")
        print("├" + "┼".join("─" * w for w in widths_oth) + "┤")

        for idx, (name, data) in enumerate(oth_list, 1):
            st_type = data.get("type", "ac")
            emoji = "🏠⚡" if st_type == "home" else ("🔌" if st_type == "dc_fast" else "🅿️")
            hw = data.get("hardware", {})
            costs = data.get("costs", {})
            net = data.get("network") or data.get("operator") or ("Tesla Wall Connector" if st_type == "home" else "3rd-Party")
            type_lbl = "Home AC" if st_type == "home" else ("DC Fast" if st_type == "dc_fast" else "Dest AC")
            pwr = f"{hw.get('max_power_kw', 0):.0f} kW" if hw.get('max_power_kw') else "-"
            hw_type = hw.get("charger_type") or "-"
            sched_str = f"${costs.get('flat_per_kwh', 0):.2f}/kWh flat" if "flat_per_kwh" in costs else "TOU rates"

            st_state = data.get("state")
            if (not st_state or st_state == "-") and data.get("lat") and data.get("lon"):
                try:
                    from find_tesla_chargers import state_from_coords
                    resolved_st, _ = state_from_coords(data.get("lat"), data.get("lon"))
                    if resolved_st:
                        st_state = resolved_st
                except Exception:
                    pass
            st_state = st_state or "-"

            row_cells = [
                pad_display(str(idx), widths_oth[0], "center"),
                pad_display(st_state, widths_oth[1], "center"),
                pad_display(f" {emoji} {name}", widths_oth[2], "left", truncate=True),
                pad_display(f" {net}", widths_oth[3], "left", truncate=True),
                pad_display(type_lbl, widths_oth[4], "center"),
                pad_display(pwr, widths_oth[5], "center"),
                pad_display(hw_type, widths_oth[6], "center"),
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
                        "Loss (kWh)": f"{s['loss_kwh']:.2f}",
                        "Efficiency (%)": f"{s['efficiency_pct']:.1f}",
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
                net_slug = clean_station_short_name(net_raw, max_length=30)

            # Clean location string / short_name
            clean_loc = ""
            reg_obj = matched_session.get("registry_obj") if matched_session else None
            if reg_obj and isinstance(reg_obj, dict):
                clean_loc = reg_obj.get("tesla_metadata", {}).get("short_name", "")
                if not clean_loc:
                    nm = reg_obj.get("name", "")
                    clean_loc = clean_station_short_name(nm)

            if not clean_loc and inv_loc:
                inv_clean_lower = inv_loc.lower().strip()
                for sc_k, sc_v in self.superchargers.items():
                    sc_meta = sc_v.get("tesla_metadata", {})
                    sc_name_clean = sc_k.lower().strip()
                    kws = [k.lower() for k in sc_meta.get("keywords", [])]
                    if inv_clean_lower == sc_name_clean or any(k in inv_clean_lower for k in kws) or sc_name_clean in inv_clean_lower:
                        clean_loc = sc_meta.get("short_name", "")
                        break

            if not clean_loc and inv_loc:
                inv_clean_lower = inv_loc.lower().strip()
                for p_name, p_data in self.places.items():
                    p_name_clean = p_name.lower().strip()
                    kws = [k.lower() for k in p_data.get("keywords", [])]
                    if inv_clean_lower == p_name_clean or any(k in inv_clean_lower for k in kws) or p_name_clean in inv_clean_lower:
                        clean_loc = clean_station_short_name(p_name)
                        break

            if not clean_loc:
                loc_source = (matched_session.get("place_name") if matched_session else "") or inv_loc
                clean_loc = clean_station_short_name(loc_source)

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

        # Print preview table
        widths = [4, 48, 80, 16, 18, 16]
        total_inner_w = sum(widths) + len(widths) - 1

        print(f"\n┌{'─' * total_inner_w}┐")
        print(f"│{pad_display(f' ⚡ {C_BOLD}CHARGING INVOICE RENAMER ({len(rename_plan)} files){C_RESET}', total_inner_w, 'left')}│")
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

        to_rename_count = 0
        for idx, item in enumerate(rename_plan, 1):
            if item["already_named"]:
                st_badge = f"{C_GREEN}ALREADY NAMED{C_RESET}"
            else:
                st_badge = f"{C_CYAN}RENAME ➔{C_RESET}"
                to_rename_count += 1

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

        if to_rename_count == 0:
            print(f"{C_GREEN}All {len(rename_plan)} invoice files are already correctly named!{C_RESET}\n")
            return

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

    def sync_to_external_drive(self):
        print(f"{C_YELLOW}ℹ️  Tessie data and charging tooling run directly from the repository and iCloud. Mounted TESLADRIVE volumes are reserved exclusively for dashcam/TeslaCam media.{C_RESET}")

# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------

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
    parser.add_argument("--unreconciled", "-u", action="store_true", help="Filter for unreconciled sessions or rate mismatches")
    parser.add_argument("--audit", action="store_true", help="List mismatches without interactive prompting")
    parser.add_argument("--since", help="Filter sessions on or after date (YYYY-MM-DD or relative: today, yesterday, monday)")
    parser.add_argument("--until", help="Filter sessions on or before date (YYYY-MM-DD)")
    
    parser.add_argument("--inspect", help="Deep-dive inspect a specific session by Charge # or Date")
    parser.add_argument("--correlation", "--compare", action="store_true", help="Display 3-way correlation audit comparing Tessie summary, detailed telemetry CSV, and Invoices")
    parser.add_argument("--list-chargers", action="store_true", help="List all registered Superchargers and 3rd-Party charging stations")
    parser.add_argument("--consolidate", action="store_true", help="Consolidate all charges into charges_master.csv")
    parser.add_argument("--rename-invoices", "--rename", action="store_true", help="Rename invoice PDFs to <date/time>_<network>_<invoice_num>_<place>.pdf")
    parser.add_argument("--rename-format", help="Custom renaming template (e.g. '{datetime}_{network}_{invoice_num}_{place}.pdf')")
    parser.add_argument("--dry-run", action="store_true", help="Preview renaming without modifying files on disk")
    parser.add_argument("--yes", "-y", action="store_true", help="Automatically confirm renaming without interactive prompt")
    parser.add_argument("--export", help="Export reconciled results to a CSV or JSON file")
    parser.add_argument("--sync", action="store_true", help="Sync tools and registries to external drive")
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

    if args.sync:
        analyzer.sync_to_external_drive()
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
        filtered = [s for s in filtered if s["emoji"] == "🏠⚡"]

    if args.unreconciled:
        filtered = [s for s in filtered if s["status"] in ["UNRECONCILED ❓", "RATE MISMATCH ⚠️", "INVOICE ONLY 📄"]]

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

    analyzer.print_summary(filtered)
    analyzer.print_sessions_table(filtered)

    if args.export:
        analyzer.export_reconciliation(args.export, filtered)

    # Interactive session selection loop: allows selecting session to inspect or manually correct rate/cost
    if sys.stdin.isatty() and not args.inspect and not args.export and not args.audit:
        while True:
            try:
                choice = input(f"{C_BOLD}Select [#] to inspect / manually correct, or [q]uit: {C_RESET}").strip().lower()
                if not choice or choice in ['q', 'quit', 'exit']:
                    break
                if choice.isdigit():
                    analyzer.inspect_session(int(choice))
                    # Refresh filtered list and reprint summary and table
                    filtered = [s for s in analyzer.reconciled_sessions if any(s["charge_index"] == orig["charge_index"] for orig in filtered)] or analyzer.reconciled_sessions
                    analyzer.print_sessions_table(filtered)
                else:
                    print("Enter a session # or 'q' to quit.")
            except (KeyboardInterrupt, EOFError):
                print()
                break


if __name__ == "__main__":
    main()
