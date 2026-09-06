# AGENTS.md — AI Agent Operating Instructions & Task Tracker

> **Notice to AI Agents**:
> This document is the primary instructions, standards, and state tracker for this repository.
> **Every agent working in this repository MUST read this file upon startup, verify its accuracy against the current codebase, and keep it up-to-date after completing work.**

---

## 1. Core Operating Directives

1. **Primary Workspace & iCloud Symlink**:
   - **Primary Workspace**: `/Users/glenn/repos/tesla` (Local filesystem clone).
   - **iCloud Symlink**: `/Users/glenn/Library/Mobile Documents/com~apple~CloudDocs/repos/tesla` is a direct symlink back to `/Users/glenn/repos/tesla`.
   - **Rationale**: Development must be done locally because (a) Antigravity cannot work directly in iCloud directories and (b) this is a Git repository.
   - **Rule on Upstream (iCloud) Changes**: If any external changes arrive via iCloud, anything newer or unique MUST be flagged and compared with the local clone, and local is updated only after the agent explains the new additions/changes to the user.

2. **Git Workflow, Commits & Security Directives**:
   - **Check with User Prior to Committing**: Prior to executing any git commit, the agent MUST check with the user, describing both:
     1. The changes made verbally in clear detail.
     2. The exact proposed commit message(s).
   - **Zero-PII Commitment Rule**: NO Personally Identifiable Information (PII) is ever committed:
     - PII includes: Personal addresses, residential coordinates, trip/drive logs, or any CSV files (all Tessie CSVs are classified as PII).
     - Non-PII includes: Public JSON files scraped from public websites (such as Tesla chargers or PlugShare registries) and sanitized `.example.json` templates.
     - Never commit `places.json`, `config.json`, or `Tessie/invoices/*.pdf`.
   - **Credential & Secret Protection**: If any string resembles an API token, password, or secret, flag it immediately for inclusion in `.gitignore`.
   - **Cadence**: Regular `git status` checks and tidy, logical git commits should occur.

3. **Terminal Execution & Permissions**:
   - Commands touching `/Users/glenn/Library/Mobile Documents/` or `/Volumes/TESLADRIVE` require sandbox bypass (`BypassSandbox: true` or equivalent direct shell permissions) due to macOS privacy and volume mount restrictions.
   - Do NOT use `cd` in tool calls; always specify full absolute working directories (`Cwd`).

4. **Continuous Maintenance of this Document**:
   - Check the **Active Requests**, **TODOs**, and **Known Bugs** sections when starting any task.
   - Update the status of completed items and add newly identified requests, bugs, or architectural decisions before concluding.

---

## 2. Table Display & UI Formatting Standards

All terminal tools (such as `Tools/tessie_drives_analyzer.py` and `Tools/tessie_charging_analyzer.py`) render rich Unicode box-drawing tables. The following rules are mandatory for all table implementations:

1. **Dynamic Column Width Measurement (No Hardcoded Cell Sizing)**:
   - Never hardcode column widths for dynamic text columns (e.g., `Notable Destinations`, `Route`, `Station / Location`, `Address`).
   - Always precompute or inspect all rows in the dataset to calculate `w_col = max(len(" Header "), max(display_len(cell) for cell in rows) + 1)`.
   - Ensure `total_inner` accommodates the full header title (`title_str`). If `display_len(title_str) + 2 > total_inner`, expand the widest column to balance the table.

2. **Visual Width vs. String Length (Unicode & Emoji Handling)**:
   - Standard Python `len()` counts UTF-8 code points, which breaks alignment when emojis (e.g., 🏠, 🕒, 🛡️, 📹, ⚡, 🔴) or wide Asian characters are displayed.
   - Always calculate visual widths using `display_len()` and format cells with `pad_display(text, target_width, align="left"|"right"|"center")`.

3. **Piped & Non-Interactive Execution Safety**:
   - Never auto-cascade through interactive drill-down menus or dump hundreds of lines of footage when `sys.stdin` is piped (e.g. `echo "q" | ./script.py`).
   - Allow `input()` to consume piped stdin cleanly, and catch `EOFError` / `KeyboardInterrupt` to exit gracefully.

---

## 3. Repository Architecture & Layout

```
tesla/
├── AGENTS.md                  # This document (AI instructions, rules, requests, TODOs, bugs)
├── README.md                  # Human-facing project overview
├── .gitignore                 # Git ignore rules (with pointer to AGENTS.md)
├── requirements.txt           # Python dependencies
├── Tessie/                    # Tessie telemetry, charging, and trip exports
│   ├── drives/                # Drive summary files & drives_master.csv
│   │   └── archive/           # Raw imported drive files (timestamp-archived)
│   ├── charges/               # Charging session summary files & charges_master.csv
│   │   └── archive/           # Raw imported charge files (timestamp-archived)
│   ├── invoices/              # Tesla Supercharger tax invoices (PDFs)
│   ├── places.json            # Tagged places, boundaries, and GPS coordinates
│   └── config.json            # Tariffs, electricity plans, and API keys
├── Tools/                     # Core CLI analyzers and utilities
│   ├── tessie_drives_analyzer.py    # Drives explorer, footage linker, and master consolidator
│   ├── tessie_charging_analyzer.py  # 3-way charging reconciler, tariff auditor, and invoice matcher
│   └── tessie_places.py             # Place tagging and geocoding utility
└── dashcam/                   # TeslaCam clips and sentry event utilities
```

---

## 4. Active Requests Tracker

| Request # | Date / Time | Summary / Description | Status | Implemented In |
|---|---|---|---|---|
| **REQ-001** | 2026-06-05 | 3-way energy & cost reconciliation for charging (Tessie vs. Car vs. Invoice/Tariff) | ✅ Complete | `Tools/tessie_charging_analyzer.py` |
| **REQ-002** | 2026-09-06 | Fix $0.00 tariff rate bug for 3rd-party chargers (Bunnings / Exploren) and align inspection card columns | ✅ Complete | `Tools/tessie_charging_analyzer.py` |
| **REQ-003** | 2026-09-06 | Separate Drive Deep Dive telemetry files from drive summary master during consolidation | ✅ Complete | `Tools/tessie_drives_analyzer.py` |
| **REQ-004** | 2026-09-06 | Scan `~/Downloads` and repo root for incoming drives and charges CSVs, auto-merge into subdirectories | ✅ Complete | `Tools/tessie_drives_analyzer.py`, `Tools/tessie_charging_analyzer.py` |
| **REQ-005** | 2026-09-06 | Break down drives list by month with high-level monthly overview menu | ✅ Complete | `Tools/tessie_drives_analyzer.py` |
| **REQ-006** | 2026-09-06 | Add "Notable Destinations" column to days table highlighting key non-home stops | ✅ Complete | `Tools/tessie_drives_analyzer.py` |
| **REQ-007** | 2026-09-06 | Transform day trips drill-down into a wide structured grid table with battery SoC % and dwell times | ✅ Complete | `Tools/tessie_drives_analyzer.py` |
| **REQ-008** | 2026-09-06 | Dynamically measure max column widths for all tables so text like Notable Destinations never overflows borders | ✅ Complete | `Tools/tessie_drives_analyzer.py` |
| **REQ-009** | 2026-09-06 | Establish `AGENTS.md` in repository root to track requests, TODOs, bugs, and agent rules | ✅ Complete | `AGENTS.md`, `.gitignore` |
| **REQ-010** | 2026-09-06 | Primary workspace transitioned to `/Users/glenn/repos/tesla`; flag & explain iCloud additions before updating local | ✅ Complete | `AGENTS.md` |
| **REQ-011** | 2026-09-06 | Symlink iCloud path back to local repo; add commit approval, zero-PII, and token protection directives | ✅ Complete | `AGENTS.md`, `.gitignore` |

---

## 5. Known Bugs & Issue Tracker

| Bug ID | Component | Description | Impact | Status / Resolution |
|---|---|---|---|---|
| **BUG-001** | Charging Analyzer | Exploren/PlugShare flat tariff rates defaulted to $0.00/kWh because extractor looked only for Supercharger tier keys. | High | **Resolved**: Updated `get_expected_tariff_rate` to extract flat and schedule rates across all provider schemas. |
| **BUG-002** | Charging Analyzer | Uneven spacing in `inspect_session` caused dollar figures and kWh amounts to misalign across rows. | Medium | **Resolved**: Enforced fixed-width label padding (`lw_fin = 27`, `lw_loss = 31`). |
| **BUG-003** | Drives Analyzer | `Notable Destinations` and `Route` columns had hardcoded widths (e.g. 36/48), causing long names to push border pipes out and ruin table borders. | High | **Resolved**: Dynamically precompute all rows and calculate column widths using `max(display_len) + 1`. |
| **BUG-004** | Drives Analyzer | Non-tty piped runs (`echo "q" \| ./script.py`) triggered automatic cascading down through all days into footage dumps. | Medium | **Resolved**: Removed hardcoded `if not sys.stdin.isatty():` drill-downs, allowing clean piped input and EOF termination. |

---

## 6. TODOs & Backlog

- [ ] **TODO-001**: Add automatic cache invalidation for places geocoding if `places.json` is updated during an active session.
- [ ] **TODO-002**: Provide an interactive CLI shortcut in `tessie_charging_analyzer.py` to batch-update mismatched tariff entries in `charges_master.csv`.
- [ ] **TODO-003**: Check and test automated linking between dashcam footage on mounted external drives and deep-dive drive telemetry timestamps.
- [ ] **TODO-004**: Add unit/smoke tests for table formatting and column width calculation to prevent future regression.

---

## 7. Verification & Update Checklist for Agents

Before completing any prompt or task, every agent must perform the following checklist:
1. **Codebase Check**: Ensure all modified files run without syntax errors (`python3 -m py_compile <file>`).
2. **Table Check**: If terminal tables were modified, verify borders with sample piped input or interactive test.
3. **Sync Check**: Copy modified files from iCloud path to local `/Users/glenn/repos/tesla/`.
4. **Docs Update**: If a request was fulfilled or a new bug/TODO was discovered, update Sections 4, 5, or 6 of this `AGENTS.md` file.
