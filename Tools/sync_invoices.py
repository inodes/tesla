#!/usr/bin/env python3
"""
sync_invoices.py - Generic PDF Invoice Ingestion & Extraction Tool
===================================================================
Discovers, inspects, extracts structured metadata from, and standardizes
the filenames and locations of PDF invoices and statements.

Includes dedicated extractors for:
  - AGL Energy statements & tax invoices (Statement_{service}_{YYYYMMDD}.pdf)
  - Tesla Supercharging tax invoices ({YYYYMMDD}_{HHMM}_Tesla_{InvoiceNo}_{Place}.pdf)
  - Exploren EV charging receipts ({YYYYMMDD}_{HHMM}_Exploren_{InvoiceNo}_{Place}.pdf)
  - Chargefox EV charging receipts ({YYYYMMDD}_{HHMM}_Chargefox_{InvoiceNo}_{Place}.pdf)
  - Generic EV Charging & generic PDF invoices

Usage:
  ./sync_invoices.py --scan                                  # Scan default invoice directories (AGL, Tesla/Exploren, Downloads)
  ./sync_invoices.py --scan ~/Downloads                      # Scan specific folder
  ./sync_invoices.py --scan --provider tesla                 # Filter Tesla Supercharger invoices
  ./sync_invoices.py --scan --provider exploren              # Filter Exploren charging receipts
  ./sync_invoices.py --scan --provider agl                   # Filter AGL power statements
  ./sync_invoices.py --scan ~/Downloads --rename             # Rename in-place
  ./sync_invoices.py --scan ~/Downloads --import             # Move to canonical data home
  ./sync_invoices.py --inspect path/to/invoice.pdf           # Deep-dive inspect single PDF
  ./sync_invoices.py --scan --json                           # Dump structured JSON
"""

import argparse
import calendar
import datetime
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from lib.invoice_common import (
        PDFTextExtractor,
        detect_and_extract_invoice,
        AVAILABLE_EXTRACTORS,
        get_extractor,
    )
    from lib.tessie_api_common import load_config
    from lib.table_formatter import pad_display
except ImportError:
    from Tools.lib.invoice_common import (
        PDFTextExtractor,
        detect_and_extract_invoice,
        AVAILABLE_EXTRACTORS,
        get_extractor,
    )
    from Tools.lib.tessie_api_common import load_config
    from Tools.lib.table_formatter import pad_display


# ANSI Color formatting
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[32m"
C_CYAN = "\033[36m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"


def resolve_default_scan_dirs() -> List[str]:
    """Resolve candidate scan folders from config or iCloud defaults."""
    cfg = load_config()
    dirs = []

    def add_dir(path_str: Optional[str]):
        if not path_str:
            return
        real_p = os.path.realpath(os.path.abspath(os.path.expanduser(path_str)))
        if os.path.isdir(real_p) and real_p not in dirs:
            dirs.append(real_p)

 # 1. Power / AGL invoices dir (config.json's directories.power_invoices -
 # genuinely optional now; BUG-131 follow-up removed the hardcoded iCloud
 # fallback that used to be duplicated here)
    add_dir(cfg.get("directories", {}).get("power_invoices"))

 # 2. EV Charging invoices dir (Tesla Supercharger, Exploren, Chargefox)
    add_dir(cfg.get("directories", {}).get("invoices"))
    add_dir("~/Library/Mobile Documents/com~apple~CloudDocs/PDF/Tesla/charging_invoices")

 # 3. Downloads folder
    add_dir("~/Downloads")

    return dirs


def set_mtime_from_invoice_metadata(path: str, meta: Dict[str, Any]) -> None:
    """REQ-142: after filing an invoice PDF under its standardized name,
    set the file's own mtime (and atime) to the date the invoice's DATA
    represents - its issue_date (YYYY-MM-DD, every extractor sets this)
    plus session_time (HHMM local, charging-invoice extractors only) when
    present - rather than leaving it at whatever moment it happened to be
    downloaded/moved/renamed on this machine. Matches the convention used
    for the one-time REQ-142 mtime backfill: date component at 00:00 UTC
    when no time-of-day is known, otherwise the known local time treated
    as UTC (good enough for sorting/browsing - these files don't carry a
    real timezone-aware timestamp field to convert from). Best-effort:
    a missing/unparseable date leaves the file's mtime untouched rather
    than raising, since a filing operation succeeding is more important
    than this cosmetic follow-up."""
    issue_date = meta.get("issue_date")
    if not issue_date:
        return
    try:
        year, month, day = (int(x) for x in issue_date.split("-"))
        hour, minute = 0, 0
        session_time = meta.get("session_time")
        if session_time and len(session_time) == 4 and session_time.isdigit():
            hour, minute = int(session_time[:2]), int(session_time[2:])
        epoch = calendar.timegm((year, month, day, hour, minute, 0, 0, 0, 0))
        os.utime(path, (epoch, epoch))
    except (ValueError, OSError):
        pass


def scan_directory_for_pdfs(dir_path: str, recursive: bool = False) -> List[str]:
    """Return all valid PDF paths in directory."""
    if not os.path.isdir(dir_path):
        return []
    pdfs = []
    try:
        if recursive:
            for root, _, files in os.walk(dir_path):
                for f in sorted(files):
                    if f.lower().endswith(".pdf") and not f.startswith("."):
                        pdfs.append(os.path.join(root, f))
        else:
            for f in sorted(os.listdir(dir_path)):
                if f.lower().endswith(".pdf") and not f.startswith("."):
                    pdfs.append(os.path.join(dir_path, f))
    except (OSError, PermissionError):
        pass
    return pdfs


def inspect_single_pdf(pdf_path: str, forced_provider: Optional[str] = None):
    """Deep-dive inspect a single PDF and display extracted fields."""
    if not os.path.isfile(pdf_path):
        print(f"{C_RED}Error: File not found: {pdf_path}{C_RESET}", file=sys.stderr)
        sys.exit(1)

    extractor, data, raw_text = detect_and_extract_invoice(pdf_path, forced_provider)
    target_fname = extractor.generate_filename(data, pdf_path)
    target_dir = extractor.resolve_target_dir(data, pdf_path)

    print(f"\n{C_BOLD}📄 PDF INVOICE INSPECTION CARD{C_RESET}")
    print("═" * 78)
    print(f"  {C_BOLD}Source File:{C_RESET}     {os.path.basename(pdf_path)}")
    print(f"  {C_BOLD}Source Path:{C_RESET}     {pdf_path}")
    print(f"  {C_BOLD}File Size:{C_RESET}       {os.path.getsize(pdf_path):,} bytes")
    print(f"  {C_BOLD}Detected As:{C_RESET}     {C_CYAN}{extractor.display_name}{C_RESET} (provider: `{extractor.name}`)")
    print(f"  {C_BOLD}Target Name:{C_RESET}     {C_GREEN}{target_fname}{C_RESET}")
    if target_dir:
        print(f"  {C_BOLD}Target Dir:{C_RESET}      {target_dir}")
    print("─" * 78)

    print(f"{C_BOLD}Extracted Metadata Fields:{C_RESET}")
    for k, v in sorted(data.items()):
        if k in ("source_path", "source_file"):
            continue
        val_str = f"${v:,.2f}" if isinstance(v, float) and "amount" in k else str(v)
        val_colored = f"{C_YELLOW}{val_str}{C_RESET}" if v is not None else f"{C_DIM}None{C_RESET}"
        print(f"  • {pad_display(k + ':', 20, 'left')} {val_colored}")

    print("─" * 78)
    print(f"{C_BOLD}Raw Text Preview (first 500 chars):{C_RESET}")
    clean_preview = " ".join(raw_text.split())[:500]
    print(f"{C_DIM}{clean_preview}...{C_RESET}\n")


def process_scanned_invoices(
    pdf_paths: List[str],
    forced_provider: Optional[str] = None,
    dry_run: bool = True,
    do_rename: bool = False,
    do_import: bool = False,
    as_json: bool = False,
):
    """Process a batch of candidate invoice PDFs."""
    if not pdf_paths:
        if not as_json:
            print(f"{C_YELLOW}No PDF files found to process.{C_RESET}")
        else:
            print(json.dumps([]))
        return

    results = []
    for p in pdf_paths:
 # In batch scan mode, detect auto first so provider filter acts as a filter
        is_single_file = (len(pdf_paths) == 1)
        ext_override = forced_provider if is_single_file else None
        extractor, data, _ = detect_and_extract_invoice(p, ext_override)

        if forced_provider and forced_provider.lower() != "auto" and not is_single_file:
            req = forced_provider.lower()
            if req == "charging":
                if extractor.name not in ("tesla", "exploren", "chargefox", "charging"):
                    continue
            elif extractor.name.lower() != req:
                continue

        target_name = extractor.generate_filename(data, p)
        target_dir = extractor.resolve_target_dir(data, p) or os.path.dirname(p)
        orig_name = os.path.basename(p)
        is_already_standard = (orig_name == target_name)

        results.append({
            "source_path": p,
            "source_file": orig_name,
            "extractor": extractor,
            "metadata": data,
            "target_name": target_name,
            "target_dir": target_dir,
            "is_standard": is_already_standard,
        })

    if as_json:
        serializable = []
        for r in results:
            serializable.append({
                "source_file": r["source_file"],
                "source_path": r["source_path"],
                "provider": r["extractor"].name,
                "target_name": r["target_name"],
                "target_dir": r["target_dir"],
                "metadata": r["metadata"],
            })
        print(json.dumps(serializable, indent=2))
        return

 # Dynamic column widths
    w_src = min(40, max(len("Source File") + 2, max(len(r["source_file"]) + 2 for r in results)))
    w_prov = max(len("Provider") + 2, max(len(r["extractor"].display_name) + 2 for r in results))
    w_dt = 13
    w_acc = max(len("Account / ID") + 2, max(len(str(r["metadata"].get("account_number") or r["metadata"].get("invoice_number") or "-")) + 2 for r in results))
    w_amt = 12
    w_tgt = min(42, max(len("Target Standard Name") + 2, max(len(r["target_name"]) + 2 for r in results)))

    widths = [w_src, w_prov, w_dt, w_acc, w_amt, w_tgt]
    total_inner_w = sum(widths) + len(widths) - 1

    header_title = f" 📄 {C_BOLD}PDF INVOICE INGESTION & BATCH DISCOVERY{C_RESET}"

    print(f"\n┌{'─'*total_inner_w}┐")
    print(f"│{pad_display(header_title, total_inner_w)}│")
    print("├" + "┬".join("─" * w for w in widths) + "┤")
    print("│" + "│".join([
        pad_display("Source File", widths[0], "center"),
        pad_display("Provider", widths[1], "center"),
        pad_display("Issue Date", widths[2], "center"),
        pad_display("Account / ID", widths[3], "center"),
        pad_display("Total AUD", widths[4], "center"),
        pad_display("Target Standard Name", widths[5], "center"),
    ]) + "│")
    print("├" + "┼".join("─" * w for w in widths) + "┤")

    renamed_count = 0
    imported_count = 0
    already_count = 0

    for r in results:
        meta = r["metadata"]
        src = r["source_file"]
        prov = r["extractor"].display_name
        dt_s = str(meta.get("issue_date") or meta.get("session_datetime") or "-")
        acc_s = str(meta.get("account_number") or meta.get("invoice_number") or "-")
        amt = meta.get("total_amount")
        amt_s = f"${amt:,.2f}" if amt is not None else "-"
        tgt = r["target_name"]

        if r["is_standard"]:
            already_count += 1
            tgt_display = f"{C_DIM}{tgt}{C_RESET}"
        else:
            tgt_display = f"{C_GREEN}{tgt}{C_RESET}"

        print("│" + "│".join([
            pad_display(f" {src}", widths[0], "left", truncate=True),
            pad_display(f" {prov}", widths[1], "left", truncate=True),
            pad_display(f"{dt_s} ", widths[2], "center", truncate=True),
            pad_display(f"{acc_s} ", widths[3], "center", truncate=True),
            pad_display(f"{amt_s} ", widths[4], "right", truncate=True),
            pad_display(f" {tgt_display}", widths[5], "left", truncate=True),
        ]) + "│")

 # Execute rename or import if not dry-run
        if not dry_run:
            src_path = r["source_path"]
            if do_import:
                dest_dir = r["target_dir"]
                os.makedirs(dest_dir, exist_ok=True)
                dest_path = os.path.join(dest_dir, tgt)
                if os.path.abspath(src_path) != os.path.abspath(dest_path):
 # Anti-clobber protection
                    if os.path.exists(dest_path) and os.path.getsize(src_path) != os.path.getsize(dest_path):
                        base, ext = os.path.splitext(tgt)
                        dest_path = os.path.join(dest_dir, f"{base}_01{ext}")
                    shutil.move(src_path, dest_path)
                    set_mtime_from_invoice_metadata(dest_path, meta)
                    imported_count += 1
            elif do_rename:
                dest_path = os.path.join(os.path.dirname(src_path), tgt)
                if os.path.abspath(src_path) != os.path.abspath(dest_path):
                    if os.path.exists(dest_path) and os.path.getsize(src_path) != os.path.getsize(dest_path):
                        base, ext = os.path.splitext(tgt)
                        dest_path = os.path.join(os.path.dirname(src_path), f"{base}_01{ext}")
                    os.rename(src_path, dest_path)
                    set_mtime_from_invoice_metadata(dest_path, meta)
                    renamed_count += 1

    print("└" + "┴".join("─" * w for w in widths) + "┘")

    if dry_run:
        print(f"\n{C_BOLD}Summary:{C_RESET} {len(results)} files scanned ({already_count} already standard, {len(results) - already_count} pending standardization).")
        print(f"👉 {C_DIM}To rename files in-place, pass --rename. To move files to canonical data homes, pass --import.{C_RESET}\n")
    else:
        if do_import:
            print(f"\n{C_GREEN}✅ Successfully imported {imported_count} invoices into canonical directories.{C_RESET}\n")
        elif do_rename:
            print(f"\n{C_GREEN}✅ Successfully standardized {renamed_count} invoice filenames.{C_RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generic PDF Invoice Ingestion & Standardization Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g_mode = parser.add_mutually_exclusive_group()
    g_mode.add_argument("--scan", nargs="?", const="", help="Scan directory for invoice PDFs (default: AGL invoices and Downloads)")
    g_mode.add_argument("--inspect", metavar="PDF", help="Deep-dive inspect single invoice PDF")

    parser.add_argument(
        "--provider",
        choices=["auto", "agl", "tesla", "exploren", "chargefox", "charging", "generic"],
        default="auto",
        help="Filter or force specific invoice provider rules (agl, tesla, exploren, chargefox, charging, generic)",
    )
    parser.add_argument("--rename", action="store_true", help="Rename unstandardized PDFs in-place")
    parser.add_argument("--import", dest="do_import", action="store_true", help="Move PDFs into provider's canonical data home")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Preview actions without modifying files")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recursively scan subdirectories")

    args = parser.parse_args()

 # Default action is --scan if no mode flag given
    if not args.inspect and args.scan is None:
        args.scan = ""

 # Dry-run is default unless --rename or --import is explicitly passed
    is_dry_run = True
    if args.rename or args.do_import:
        is_dry_run = False
    if args.dry_run is not None:
        is_dry_run = args.dry_run

    if args.inspect:
        inspect_single_pdf(args.inspect, args.provider)
        return

 # Scan mode
    scan_target = args.scan
    pdf_list = []
    if scan_target:
        exp_target = os.path.abspath(os.path.expanduser(scan_target))
        if os.path.isfile(exp_target) and exp_target.lower().endswith(".pdf"):
            pdf_list = [exp_target]
        elif os.path.isdir(exp_target):
            pdf_list = scan_directory_for_pdfs(exp_target, args.recursive)
        else:
            print(f"{C_RED}Error: Path not found: {scan_target}{C_RESET}", file=sys.stderr)
            sys.exit(1)
    else:
        for d in resolve_default_scan_dirs():
            pdf_list.extend(scan_directory_for_pdfs(d, args.recursive))

 # Deduplicate by realpath while preserving order
    seen_paths = set()
    deduped_list = []
    for p in pdf_list:
        rp = os.path.realpath(p)
        if rp not in seen_paths:
            seen_paths.add(rp)
            deduped_list.append(p)
    pdf_list = deduped_list

    process_scanned_invoices(
        pdf_paths=pdf_list,
        forced_provider=args.provider,
        dry_run=is_dry_run,
        do_rename=args.rename,
        do_import=args.do_import,
        as_json=args.json,
    )


if __name__ == "__main__":
    main()
