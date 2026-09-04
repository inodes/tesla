#!/usr/bin/env python3
"""
Compatibility wrapper forwarding to find_tesla_chargers.py
"""
import os
import sys
import subprocess

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(script_dir, "find_tesla_chargers.py")
    if not os.path.isfile(target_script):
        print(f"Error: Could not find {target_script}", file=sys.stderr)
        sys.exit(1)
    
    cmd = [sys.executable, target_script] + sys.argv[1:]
    sys.exit(subprocess.call(cmd))

if __name__ == "__main__":
    main()
