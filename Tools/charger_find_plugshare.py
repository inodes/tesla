#!/usr/bin/env python3
"""
🔌 PlugShare Charger Tool (DEPRECATED WRAPPER) 🔌
=================================================
DEPRECATION NOTICE:
This standalone script is deprecated. All PlugShare discovery, inspection,
search, pricing extraction, and registry management capabilities have been
consolidated into:
    ./Tools/charger_find.py
    ./Tools/providers/chargers_plugshare.py

This wrapper prints a deprecation banner and delegates execution to charger_find.py.
"""

import os
import sys
import subprocess

C_YELLOW = "\033[33m"
C_BOLD = "\033[1m"
C_CYAN = "\033[36m"
C_RESET = "\033[0m"


def main():
    print(f"\n{C_YELLOW}{C_BOLD}[DEPRECATED]{C_RESET} {C_YELLOW}./Tools/charger_find_plugshare.py is deprecated.{C_RESET}")
    print(f"👉 Please use {C_BOLD}./Tools/charger_find.py{C_RESET} instead.\n")

    _tools_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(_tools_dir, "charger_find.py")

    # Map legacy arguments to charger_find.py arguments if needed
    args = sys.argv[1:]
    mapped_args = []
    has_sync = False
    has_query = False
    has_inspect = False

    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--country", "-country"):
            mapped_args.append("--sync-plugshare")
            has_sync = True
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 1  # Skip country value (e.g. 'Australia')
        elif a in ("--inspect", "--scrape"):
            mapped_args.append("--plugshare")
            has_inspect = True
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 1
                mapped_args.append(args[i])
        elif a in ("--search", "-s", "-q", "--query"):
            has_query = True
            mapped_args.append("-q")
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 1
                mapped_args.append(args[i])
        elif a == "--all-types":
            # Handled by default in charger_find.py
            pass
        elif a == "--plugshare":
            has_inspect = True
            mapped_args.append(a)
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 1
                mapped_args.append(args[i])
        else:
            mapped_args.append(a)
        i += 1

    # If --near, --state, or --add-all was specified without an explicit action, default to --sync-plugshare
    if not has_inspect and not has_query and not has_sync and not ("--from-history" in mapped_args or "--refresh-prices" in mapped_args):
        if any(x in mapped_args for x in ("--near", "-a", "--address", "--state", "--add-all")):
            mapped_args.insert(0, "--sync-plugshare")

    cmd = [sys.executable, target_script] + mapped_args
    try:
        res = subprocess.run(cmd)
        sys.exit(res.returncode)
    except KeyboardInterrupt:
        sys.exit(130)



if __name__ == "__main__":
    main()
