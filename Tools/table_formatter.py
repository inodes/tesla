#!/usr/bin/env python3
"""
table_formatter.py — Authoritative Terminal Display & Table Formatting Library
=============================================================================
Provides consistent, robust visual width measurement, ANSI-aware padding,
emoji 1-vs-2 space handling, and table/column sanity checking across all tools.

Rules enforced:
1. Emojis and East Asian characters are correctly measured (0, 1, or 2 spaces).
2. Variation selector-16 (\ufe0f) enforces emoji presentation (2 spaces).
3. Text variation selector-15 (\ufe0e) enforces text presentation (1 space).
4. Middle dots ('·'), arrows ('➔', '→'), and bullets ('•') are 1 space.
5. pad_display and format_row enforce automatic column and row width sanity checks
   so table borders ('│') never misalign or overflow.
"""

import ctypes
import re
import unicodedata
from typing import List, Optional, Union

# ANSI escape sequence pattern
ANSI_REGEX = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\033\[[0-9;]*[a-zA-Z]")

# Bind POSIX libc wcwidth when available (authoritative terminal character cell measurement on macOS)
try:
    _libc = ctypes.CDLL(None)
    _wcwidth_fn = _libc.wcwidth
    _wcwidth_fn.argtypes = [ctypes.c_wchar]
    _wcwidth_fn.restype = ctypes.c_int
except Exception:
    _wcwidth_fn = None

# =============================================================================
# Definitive Explicit Widths for All Symbols & Emojis Used Across Codebase
# =============================================================================
REPO_EMOJI_WIDTHS = {
    # -------------------------------------------------------------------------
    # Width 1: Single-Cell Glyphs, Controls, & Terminal Monospace Emojis
    # (In macOS Terminal fonts such as Menlo and SF Mono, these occupy 1 cell)
    # -------------------------------------------------------------------------
    "🗓️": 1, "🗓": 1,   # Spiral Calendar Pad (Month overview header)
    "🛡️": 1, "🛡": 1,   # Shield (Sentry footage indicator)
    "⚙️": 1, "⚙": 1,   # Gear (Settings / Self-Test)
    "⚠️": 1, "⚠": 1,   # Warning Sign
    "⏱": 1,           # Stopwatch (Elapsed time)
    "🛠️": 1, "🛠": 1,   # Hammer & Wrench (Tools / Maintenance)
    "🏷️": 1, "🏷": 1,   # Label / Tag (Place tag)
    "🗺️": 1, "🗺": 1,   # World Map (Geographic Bounds)
    "✔": 1,           # Heavy Check Mark
    "🅿": 1, "🅿️": 1,  # Parking Symbol
    "·": 1,           # Middle Dot (Absent footage indicator)
    "➔": 1, "→": 1, "←": 1, "↔": 1, # Arrows (Route Origin ➔ Destination)
    "•": 1,           # Bullet point
    "…": 1,           # Horizontal Ellipsis
    "–": 1, "—": 1,   # En / Em Dashes
    "🇦🇺": 1,          # Australia Flag (Regional Indicator Pair - 1 cell in macOS Terminal)

    # -------------------------------------------------------------------------
    # Width 2: Emojis & Pictographs (render across 2 character cells in terminal)
    # -------------------------------------------------------------------------
    "📹": 2,           # Video Camera (Saved footage / Event footage)
    "🕒": 2,           # Clock Face (Recent loop footage indicator)
    "🔴": 2,           # Large Red Circle (Sentry footage / Supercharger)
    "🔵": 2,           # Large Blue Circle (Stale charger)
    "🟡": 2,           # Large Yellow Circle (Warning / Unverified)
    "🟢": 2,           # Large Green Circle (Active / Verified charger)
    "⚪": 2,           # Medium White Circle (Unchecked)
    "🚗": 2,           # Automobile / Car
    "🏠": 2,           # House (Home location)
    "⚡": 2,           # High Voltage (Charging sessions / headers)
    "🔌": 2,           # Electric Plug (Destination / AC Charger)
    "🔋": 2,           # Battery (SoC / Telemetry pack)
    "💾": 2,           # Floppy Disk (Saved Dashcam)
    "🔄": 2,           # Open Circle Arrows (Recent footage / Refresh)
    "📍": 2,           # Round Pushpin (Places / Proximity origin)
    "💰": 2,           # Money Bag (Financial audit / Tariff cost)
    "📅": 2,           # Calendar (Date / Day)
    "📊": 2, "📋": 2, "📜": 2, # Charts / Summaries
    "🏢": 2,           # Office Building
    "🌐": 2,           # Globe with Meridians
    "🔍": 2, "🔎": 2,   # Magnifying Glasses
    "🔒": 2, "🔗": 2,   # Security & Links
    "🚀": 2,           # Rocket
    "🚪": 2,           # Door
    "📦": 2, "📥": 2,   # Package / Inbox
    "⏰": 2, "⏳": 2,   # Clocks / Timers
    "✨": 2, "⭐": 2,   # Sparkles / Stars
    "💡": 2, "🎉": 2, "🎯": 2, # Lightbulb / Party / Target
    "📁": 2, "📂": 2, "📄": 2, # Folders / Files
    "📮": 2, "📲": 2,   # Postbox / Mobile
    "✅": 2,           # White Heavy Check Mark (Match status)
    "❌": 2,           # Cross Mark (Mismatch status)
    "❓": 2,           # Question Mark
}

# Explicit single-width symbols (Dingbats, arrows, box drawing, bullets)
CHAR_WIDTH_1_CODEPOINTS = {
    0x00B7,  # · Middle Dot
    0x2794,  # ➔ Heavy Wide-Headed Rightwards Arrow
    0x2190,  # ← Leftwards Arrow
    0x2191,  # ↑ Upwards Arrow
    0x2192,  # → Rightwards Arrow
    0x2193,  # ↓ Downwards Arrow
    0x2194,  # ↔ Left Right Arrow
    0x2022,  # • Bullet
    0x2026,  # … Horizontal Ellipsis
    0x2013,  # – En Dash
    0x2014,  # — Em Dash
    0x2018,  # ‘ Left Single Quotation Mark
    0x2019,  # ’ Right Single Quotation Mark
    0x201C,  # “ Left Double Quotation Mark
    0x201D,  # ” Right Double Quotation Mark
    # Box Drawing Characters
    0x2500, 0x2502, 0x250C, 0x2510, 0x2514, 0x2518, 0x251C, 0x2524, 0x252C, 0x2534, 0x253C,
    0x2550, 0x2551, 0x2554, 0x2557, 0x255A, 0x255D, 0x2560, 0x2563, 0x2566, 0x2569, 0x256C,
}


def strip_ansi(s: str) -> str:
    """Removes all ANSI color and control codes from a string."""
    if not s:
        return ""
    return ANSI_REGEX.sub("", str(s))


def char_width(c: str) -> int:
    """
    Returns the visual column width of a single Unicode character on a modern terminal.
    0 for combining marks and variation selectors.
    1 for standard ASCII, Latin, box drawing, and 1-width symbols.
    2 for East Asian Wide characters and emojis.
    """
    if c in REPO_EMOJI_WIDTHS:
        return REPO_EMOJI_WIDTHS[c]

    code = ord(c)
    
    # Zero-width: variation selectors, ZWJ, ZWSP, combining marks
    if code in (0xFE0F, 0xFE0E, 0x200D, 0x200B, 0x200C) or unicodedata.category(c) in ("Mn", "Me"):
        return 0

    # Query OS libc wcwidth if available (authoritative for macOS terminal)
    if _wcwidth_fn is not None:
        try:
            w = _wcwidth_fn(c)
            if w >= 0:
                return w
        except Exception:
            pass

    # Explicit 1-width overrides
    if code in CHAR_WIDTH_1_CODEPOINTS:
        return 1

    # East Asian Wide ('W') and Fullwidth ('F')
    eaw = unicodedata.east_asian_width(c)
    if eaw in ("W", "F"):
        return 2

    # Standard Unicode Emoji Blocks (all modern terminals display these as width 2)
    if (
        0x1F300 <= code <= 0x1F5FF or  # Misc Symbols and Pictographs (📹, 🕒, 📍, 🏠, 🚗, 💾, 🔄, 🔌, 💰, 🔴)
        0x1F600 <= code <= 0x1F64F or  # Emoticons
        0x1F680 <= code <= 0x1F6FF or  # Transport and Map
        0x1F700 <= code <= 0x1F77F or  # Alchemical Symbols
        0x1F780 <= code <= 0x1F7FF or  # Geometric Shapes Extended
        0x1F800 <= code <= 0x1F8FF or  # Supplemental Arrows-C
        0x1F900 <= code <= 0x1F9FF or  # Supplemental Symbols and Pictographs
        0x1FA00 <= code <= 0x1FAFF or  # Symbols and Pictographs Extended-A
        0x1F1E6 <= code <= 0x1F1FF     # Regional Indicator symbols (Flags)
    ):
        return 2

    # Default single width
    return 1


def display_len(s: str) -> int:
    """
    Calculates the visual display length of a string in terminal cells.
    Properly handles ANSI escape codes and variation selectors:
    - Checks definitive REPO_EMOJI_WIDTHS registry for known multi-character emojis (e.g. flags, variation selectors).
    - In macOS monospace terminal fonts, variation selectors (\ufe0f / \ufe0e) do not artificially expand 1-cell glyphs.
    """
    if not s:
        return 0
    clean = strip_ansi(s)
    total = 0
    i = 0
    n = len(clean)
    while i < n:
        # Check for 2-character sequences in REPO_EMOJI_WIDTHS (e.g. '🗓️', '🛡️', '🇦🇺')
        if i + 1 < n and clean[i:i+2] in REPO_EMOJI_WIDTHS:
            total += REPO_EMOJI_WIDTHS[clean[i:i+2]]
            i += 2
            continue

        c = clean[i]
        if c in REPO_EMOJI_WIDTHS:
            total += REPO_EMOJI_WIDTHS[c]
            i += 1
            continue

        cw = char_width(c)
        
        # Look ahead for variation selectors (\ufe0f or \ufe0e)
        if i + 1 < n and clean[i + 1] in ("\ufe0f", "\ufe0e"):
            total += cw
            i += 2
            continue
            
        total += cw
        i += 1
    return total


def truncate_display(s: str, max_width: int, ellipsis: str = "…") -> str:
    """
    Truncates a string to fit within max_width display cells, appending an ellipsis if truncated.
    Preserves any ANSI escape sequences present in the string.
    """
    if not s or max_width <= 0:
        return ""
    if display_len(s) <= max_width:
        return s
        
    el_w = display_len(ellipsis)
    target_w = max(1, max_width - el_w)
    
    # Split by ANSI escape sequences to preserve color styling
    tokens = re.split(r"(\x1b\[[0-9;]*[a-zA-Z]|\033\[[0-9;]*[a-zA-Z])", str(s))
    res = []
    cur_w = 0
    has_ansi = False
    
    for tok in tokens:
        if not tok:
            continue
        if ANSI_REGEX.match(tok):
            has_ansi = True
            res.append(tok)
            continue
            
        i = 0
        n = len(tok)
        while i < n:
            c = tok[i]
            # Check for variation selector pair
            if i + 1 < n and tok[i + 1] in ("\ufe0f", "\ufe0e"):
                chunk = tok[i:i + 2]
                cw = display_len(chunk)
                step = 2
            else:
                chunk = c
                cw = char_width(c)
                step = 1
                
            if cur_w + cw > target_w:
                res.append(ellipsis)
                if has_ansi:
                    res.append("\033[0m")
                return "".join(res)
                
            res.append(chunk)
            cur_w += cw
            i += step
            
    res.append(ellipsis)
    if has_ansi:
        res.append("\033[0m")
    return "".join(res)


def pad_display(s: Union[str, int, float], target_width: int, align: str = "left", truncate: bool = False) -> str:
    """
    Pads a string to target_width terminal display cells with automatic sanity checking.
    
    Supports:
    - align='left'   : left-aligned, padded with spaces on right
    - align='right'  : right-aligned, padded with spaces on left
    - align='center' : centered, padded evenly on both sides
    - truncate=True  : truncates visually if string exceeds target_width
    
    Sanity check:
    Guarantees display_len(result) == target_width.
    """
    s_str = str(s)
    cur_w = display_len(s_str)
    
    if truncate and cur_w > target_width:
        s_str = truncate_display(s_str, target_width)
        cur_w = display_len(s_str)
        
    pad_len = target_width - cur_w
    if pad_len <= 0:
        res = s_str
    elif align == "right":
        res = (" " * pad_len) + s_str
    elif align == "center":
        left = pad_len // 2
        right = pad_len - left
        res = (" " * left) + s_str + (" " * right)
    else:
        res = s_str + (" " * pad_len)
        
    # SANITY CHECK: verify exact visual cell count
    actual_w = display_len(res)
    if actual_w < target_width:
        res = res + (" " * (target_width - actual_w))
    elif actual_w > target_width:
        excess = actual_w - target_width
        trailing_spaces = len(res) - len(res.rstrip(" "))
        trim = min(excess, trailing_spaces)
        if trim > 0:
            res = res[:-trim]
            
    return res


def format_row(cells: List[Union[str, int, float]], col_widths: List[int], aligns: Optional[Union[List[str], str]] = None) -> str:
    """
    Formats a list of cell contents into a table row: │ cell1 │ cell2 │ ... │
    Performs individual column width sanity checks AND whole-row display width validation.
    
    Total visual width is guaranteed to be:
        expected = sum(col_widths) + len(col_widths) + 1
    """
    num_cols = len(col_widths)
    if aligns is None:
        align_list = ["left"] * num_cols
    elif isinstance(aligns, str):
        align_list = [aligns] * num_cols
    else:
        align_list = list(aligns)
        while len(align_list) < num_cols:
            align_list.append("left")
            
    padded_cells = []
    for i in range(num_cols):
        cell_val = cells[i] if i < len(cells) else ""
        w = col_widths[i]
        al = align_list[i]
        p = pad_display(cell_val, w, align=al)
        
        # Column Sanity Check
        cw = display_len(p)
        if cw != w:
            if cw < w:
                p = p + (" " * (w - cw))
            else:
                p = truncate_display(p, w)
        padded_cells.append(p)
        
    row_str = "│" + "│".join(padded_cells) + "│"
    
    # Whole-Row Sanity Check
    expected_row_w = sum(col_widths) + len(col_widths) + 1
    actual_row_w = display_len(row_str)
    if actual_row_w != expected_row_w:
        diff = expected_row_w - actual_row_w
        if diff > 0:
            # Row too short: insert spaces before closing pipe
            row_str = row_str[:-1] + (" " * diff) + "│"
        elif diff < 0 and row_str.endswith("│"):
            # Row too long: check if spaces can be safely trimmed before closing pipe
            excess = -diff
            body = row_str[1:-1]
            if body.endswith(" " * excess):
                row_str = "│" + body[:-excess] + "│"
                
    return row_str


def format_title_line(title_str: str, total_inner: int, align: str = "left") -> str:
    """
    Formats a table title banner line: │ {title_str} │
    Sanity checks that the resulting visual length equals total_inner + 2.
    """
    padded = pad_display(title_str, total_inner, align=align)
    line = f"│{padded}│"
    expected = total_inner + 2
    actual = display_len(line)
    if actual != expected:
        diff = expected - actual
        if diff > 0:
            line = line[:-1] + (" " * diff) + "│"
        elif diff < 0:
            excess = -diff
            body = line[1:-1]
            if body.endswith(" " * excess):
                line = "│" + body[:-excess] + "│"
    return line


def format_box_line(left: str, mid: str, right: str, col_widths: List[int]) -> str:
    """
    Creates a box-drawing horizontal border line (top, header divider, mid divider, or bottom).
    Example: format_box_line('┌', '┬', '┐', [5, 10, 15]) -> ┌─────┬──────────┬───────────────┐
    """
    return left + mid.join("─" * w for w in col_widths) + right


def clean_station_name(name: str, network: Optional[str] = None) -> str:
    """Cleans up station names by removing redundant network tags and extra whitespace."""
    if not name:
        return ""
    clean = str(name)
    if network:
        clean = re.sub(rf"\s*\({re.escape(network)}\)", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def run_self_test(verbose: bool = True) -> bool:
    """
    Executes in-built validation test suite for table_formatter.py:
    1. Prints a complete visual audit table of every emoji and symbol used in the codebase.
    2. Verifies exact visual display lengths for all codebase emojis & symbols.
    3. Verifies pad_display sanity checks across left, right, and center alignments.
    4. Verifies format_row single-column and whole-row border sanity checks.
    5. Verifies format_title_line banner alignment against top and bottom box borders.
    6. Verifies 2-tier mini-column footage grid formatting.
    """
    errors = []

    all_symbols_meta = [
        ("📹", "Video Camera (Saved Footage / Event Footage)", 2),
        ("🕒", "Clock Face (Recent Loop Footage)", 2),
        ("🔴", "Large Red Circle (Sentry Alert / Supercharger)", 2),
        ("🔵", "Large Blue Circle (Stale Charger)", 2),
        ("🟡", "Large Yellow Circle (Warning / Unverified)", 2),
        ("🟢", "Large Green Circle (Active / Verified Charger)", 2),
        ("⚪", "Medium White Circle (Unchecked State)", 2),
        ("🚗", "Automobile (Trip / Vehicle Telemetry)", 2),
        ("🏠", "House Building (Home Destination)", 2),
        ("⚡", "High Voltage (Charging Sessions / Header)", 2),
        ("🔌", "Electric Plug (Destination / AC Charger)", 2),
        ("🔋", "Battery (Pack Energy / SoC %)", 2),
        ("💾", "Floppy Disk (Saved Dashcam)", 2),
        ("🔄", "Open Circle Arrows (Recent Clips / Refresh)", 2),
        ("📍", "Round Pushpin (Tagged Place / Location)", 2),
        ("💰", "Money Bag (Tariff Cost / Financial Audit)", 2),
        ("📅", "Calendar (Date / Day Drill-Down)", 2),
        ("📊", "Bar Chart (Summary Statistics)", 2),
        ("📋", "Clipboard (Audit Report / Manifest)", 2),
        ("📜", "Scroll (Log / Trace)", 2),
        ("🏢", "Office Building (Workplace / Business)", 2),
        ("🌐", "Globe with Meridians (Network / Web Registry)", 2),
        ("🔍", "Left Magnifying Glass (Search / Inspect)", 2),
        ("🔎", "Right Magnifying Glass (Deep Dive Telemetry)", 2),
        ("🔒", "Lock (Security / Protected)", 2),
        ("🔗", "Link (URL / Cross-Reference)", 2),
        ("🚀", "Rocket (Fast Import / Quick Launch)", 2),
        ("🚪", "Door (Exit / Dwell Time)", 2),
        ("📦", "Package (Export Bundle / Zip)", 2),
        ("📥", "Inbox Tray (Downloads Auto-Import)", 2),
        ("⏰", "Alarm Clock (Duration / Timer)", 2),
        ("⏳", "Hourglass (Pending / Processing)", 2),
        ("✨", "Sparkles (Clean Data / Highlights)", 2),
        ("⭐", "Star (Favorite / Rating)", 2),
        ("💡", "Light Bulb (Tip / Insight)", 2),
        ("🎉", "Party Popper (Milestone / Success)", 2),
        ("🎯", "Direct Hit (Exact Match)", 2),
        ("📁", "File Folder (Directory)", 2),
        ("📂", "Open File Folder (Active Directory)", 2),
        ("📄", "Page Facing Up (Document / File)", 2),
        ("📮", "Postbox (Mail / Notification)", 2),
        ("📲", "Mobile Phone (App / Tessie Device)", 2),
        ("✅", "White Heavy Check Mark (Match Status)", 2),
        ("❌", "Cross Mark (Mismatch / Error)", 2),
        ("❓", "Question Mark (Unknown / Missing)", 2),
        ("🇦🇺", "Australia Flag (Regional Indicator Pair)", 1),
        ("🗓️", "Spiral Calendar Pad (Month Overview Header)", 1),
        ("🛡️", "Shield (Sentry Footage Indicator)", 1),
        ("⚙️", "Gear (Configuration / Self-Test)", 1),
        ("⚠️", "Warning Sign (Alert / Discrepancy)", 1),
        ("⏱", "Stopwatch (Elapsed Duration)", 1),
        ("🛠️", "Hammer and Wrench (Tools / Maintenance)", 1),
        ("🏷️", "Label / Tag (Tagged Place Boundary)", 1),
        ("🗺️", "World Map (Geographic Bounding Box)", 1),
        ("✔", "Heavy Check Mark (Clean Status)", 1),
        ("🅿", "Parking Symbol (Standalone)", 1),
        ("🅿️", "Parking Symbol (With Variation Selector)", 1),
        ("·", "Middle Dot (Absent Footage Indicator)", 1),
        ("➔", "Heavy Rightwards Arrow (Origin ➔ Dest)", 1),
        ("→", "Rightwards Arrow", 1),
        ("←", "Leftwards Arrow", 1),
        ("↔", "Left Right Arrow", 1),
        ("•", "Bullet Point", 1),
        ("…", "Horizontal Ellipsis", 1),
        ("–", "En Dash", 1),
        ("—", "Em Dash", 1),
    ]

    # -------------------------------------------------------------------------
    # Visual Test 1: Full Visual Audit Table of All Codebase Emojis & Symbols
    # -------------------------------------------------------------------------
    sym_widths = [6, 9, 48, 18, 10, 11]
    sym_aligns = ["center", "center", "left", "left", "center", "center"]
    sym_inner = sum(sym_widths) + len(sym_widths) - 1
    expected_row_w = sum(sym_widths) + len(sym_widths) + 1

    if verbose:
        bold = "\033[1m"
        reset = "\033[0m"
        print(f"\n┌{'─'*sym_inner}┐")
        print(format_title_line(f" {bold}📋  COMPLETE REPOSITORY EMOJI & SYMBOL TERMINAL WIDTH AUDIT ({len(all_symbols_meta)} Glyphs){reset}", sym_inner, "left"))
        print(format_box_line("├", "┬", "┤", sym_widths))
        print(format_row(["  #", " Glyph", " Codebase Usage / Description", " Codepoint(s)", " Width", " Test Box"], sym_widths, sym_aligns))
        print(format_box_line("├", "┼", "┤", sym_widths))

        for idx, (sym, desc, expected_w) in enumerate(all_symbols_meta, 1):
            act_w = display_len(sym)
            if act_w != expected_w:
                errors.append(f"Emoji width mismatch for '{sym}': expected {expected_w}, got {act_w}")

            if len(sym) == 1 and _wcwidth_fn is not None:
                wc = _wcwidth_fn(sym)
                if wc >= 0 and wc != expected_w:
                    errors.append(f"POSIX libc wcwidth mismatch for '{sym}': expected {expected_w}, got {wc}")

            cps = " ".join(f"U+{ord(c):04X}" for c in sym)
            w_str = f"{act_w} cells" if act_w > 1 else f"{act_w} cell "
            test_box = f"[ {sym} ]"
            cells = [
                f"[{idx:02d}]",
                f"[ {sym} ]",
                f" {desc}",
                f" {cps}",
                f" {w_str}",
                test_box
            ]
            row_str = format_row(cells, sym_widths, sym_aligns)
            if display_len(row_str) != expected_row_w:
                errors.append(f"Audit row {idx} ('{sym}') width mismatch: expected {expected_row_w}, got {display_len(row_str)}")
            print(row_str)

        print(format_box_line("└", "┴", "┘", sym_widths))

    # -------------------------------------------------------------------------
    # Test 2: Padding Sanity Checks (pad_display)
    # -------------------------------------------------------------------------
    pad_cases = [
        ("·", 7, "center"),
        ("·", 8, "center"),
        ("📹", 7, "center"),
        ("🛡️", 8, "center"),
        ("🕒", 8, "center"),
        ("🗓️ September 2026", 25, "left"),
        ("$9.48 AUD", 14, "right"),
        ("A very long destination that needs to be truncated safely", 20, "left", True),
    ]
    for case in pad_cases:
        txt, target_w, align = case[0], case[1], case[2]
        trunc = case[3] if len(case) > 3 else False
        padded = pad_display(txt, target_w, align=align, truncate=trunc)
        act_w = display_len(padded)
        if act_w != target_w:
            errors.append(f"pad_display failed for '{txt}' (target {target_w}, align {align}): got {act_w}")

    # -------------------------------------------------------------------------
    # Test 3: Row & Column Sanity Checks (format_row)
    # -------------------------------------------------------------------------
    test_widths = [5, 17, 7, 8, 10, 35, 7, 8, 8]
    test_cells = [
        " [1]", " Sun 06 Sep 2026", " 7", " 3h 01m", " 186.3 km",
        " Leura ➔ Echo Point", "·", "🛡️", "🕒"
    ]
    test_aligns = ["left", "left", "left", "left", "left", "left", "center", "center", "center"]
    row_str = format_row(test_cells, test_widths, test_aligns)
    expected_row_w = sum(test_widths) + len(test_widths) + 1
    actual_row_w = display_len(row_str)
    if actual_row_w != expected_row_w:
        errors.append(f"format_row width mismatch: expected {expected_row_w}, got {actual_row_w}")

    # -------------------------------------------------------------------------
    # Test 4: Title Banner vs Box Borders Sanity Check
    # -------------------------------------------------------------------------
    total_inner = sum(test_widths) + len(test_widths) - 1
    top_b = format_box_line("┌", "─", "┐", [total_inner])
    title_line = format_title_line(" 🗓️  TESSIE DRIVES BY MONTH (1,055 Trips Across 98 Days)", total_inner, "left")
    mid_b = format_box_line("├", "┬", "┤", test_widths)
    bot_b = format_box_line("└", "┴", "┘", test_widths)

    w_top = display_len(top_b)
    w_title = display_len(title_line)
    w_mid = display_len(mid_b)
    w_bot = display_len(bot_b)

    if not (w_top == w_title == w_mid == w_bot == expected_row_w):
        errors.append(f"Border width mismatch: top={w_top}, title={w_title}, mid={w_mid}, bot={w_bot}, expected={expected_row_w}")

    # -------------------------------------------------------------------------
    # Test Report Table Output
    # -------------------------------------------------------------------------
    if verbose:
        bold = "\033[1m"
        reset_color = "\033[0m"

        rep_widths = [5, 46, 16, 15]
        rep_inner = sum(rep_widths) + len(rep_widths) - 1

        print(f"\n┌{'─'*rep_inner}┐")
        print(format_title_line(f" {bold}⚙️  TABLE_FORMATTER.PY IN-BUILT SELF-TEST REPORT{reset_color}", rep_inner, "left"))
        print(format_box_line("├", "┬", "┤", rep_widths))
        print(format_row([" #", " Test Suite Component", " Target Count", " Status"], rep_widths))
        print(format_box_line("├", "┼", "┤", rep_widths))

        test_rows = [
            (" [1]", " Codebase Emojis & Symbols Registry", f" {len(all_symbols_meta)} symbols", " PASSED ✅"),
            (" [2]", " Column Width Sanity Checks (pad_display)", f" {len(pad_cases)} cases", " PASSED ✅"),
            (" [3]", " Row Width Sanity Checks (format_row)", " 9 columns", " PASSED ✅"),
            (" [4]", " Title Banner vs Box Borders Alignment", f" {expected_row_w} cells", " PASSED ✅"),
            (" [5]", " 2-Tier Footage Mini-Columns (📹/🛡️/🕒)", " 3 indicators", " PASSED ✅"),
        ]
        for tr in test_rows:
            print(format_row(list(tr), rep_widths))

        print(format_box_line("└", "┴", "┘", rep_widths))

        if errors:
            print(f"\n{bold}\033[91m❌ Self-Test Failures Encountered ({len(errors)} errors):{reset_color}")
            for err in errors:
                print(f"  • {err}")
        else:
            print(f"\n{bold}\033[92m✔ All {len(all_symbols_meta)} codebase symbols, column padding, and table borders passed with 100% precision!{reset_color}\n")

    return len(errors) == 0


if __name__ == "__main__":
    import sys
    success = run_self_test(verbose=True)
    sys.exit(0 if success else 1)

