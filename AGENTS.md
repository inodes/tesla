# AGENTS.md — AI Agent Operating Instructions & Task Tracker

> **Notice to AI Agents**:
> This document is the primary instructions, standards, and state tracker for this repository.
> **Every agent working in this repository MUST read this file upon startup, verify its accuracy against the current codebase, and keep it up-to-date after completing work.**

---

## 1. Core Operating Directives

1. **Primary Workspace & iCloud Symlink**:
   - **Primary Workspace**: `$HOME/repos/tesla` (or `~/repos/tesla`, local filesystem clone).
   - **iCloud Symlink**: `~/Library/Mobile Documents/com~apple~CloudDocs/repos/tesla` is a direct symlink back to `$HOME/repos/tesla`.
   - **Rationale**: Development must be done locally because (a) Antigravity cannot work directly in iCloud directories and (b) this is a Git repository.
   - **Rule on Upstream (iCloud) Changes**: If any external changes arrive via iCloud, anything newer or unique MUST be flagged and compared with the local clone, and local is updated only after the agent explains the new additions/changes to the user.

2. **Git Workflow, Commits & Security Directives**:
   - **Check with User Prior to Committing**: Prior to executing any git commit, the agent MUST check with the user, describing both:
     1. The changes made verbally in clear detail.
     2. The exact proposed commit message(s).
   - **Zero-PII Commitment Rule**: NO Personally Identifiable Information (PII) is ever committed (including in documentation, commit messages, or `AGENTS.md` itself):
     - Never include local usernames in documentation or scripts; always use `$USER`, `$HOME`, or `~`.
     - PII includes: Personal addresses, residential coordinates, trip/drive logs, or any CSV files (all Tessie CSVs are classified as PII).
     - Non-PII includes: Public JSON files scraped from public websites (such as Tesla chargers or PlugShare registries) and sanitized `.example.json` templates.
     - Never commit `places.json`, `config.json`, or `Tessie/invoices/*.pdf`.
   - **Credential & Secret Protection**: If any string resembles an API token, password, or secret, flag it immediately for inclusion in `.gitignore`.
   - **Cadence**: Regular `git status` checks and tidy, logical git commits should occur.

3. **Terminal Execution & Permissions**:
   - Commands touching `~/Library/Mobile Documents/` or `/Volumes/TESLADRIVE` require sandbox bypass (`BypassSandbox: true` or equivalent direct shell permissions) due to macOS privacy and volume mount restrictions.
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

3. **Centralized Table & Display Standards (`Tools/table_formatter.py`)**:
   - All tools MUST import formatting utilities from `Tools/table_formatter.py` (`char_width`, `display_len`, `pad_display`, `truncate_display`, `format_row`, `format_title_line`, `format_box_line`).
   - **Emoji 0 vs 1 vs 2 Spaces Handling**:
     - `0 spaces`: Variation selectors (`\ufe0f`, `\ufe0e`), zero-width joiners/spaces (`\u200d`, `\u200b`, `\u200c`), and combining marks.
     - `1 space`: Standard ASCII, Latin, box drawing (`│`, `─`, `┌`, etc.), arrows (`➔`, `→`), bullets (`•`, `·`), standalone enclosed alphanumerics (`🅿`), and monospace emojis whose cursor advance in macOS Terminal libc is 1 cell (`🗓️`, `🛡️`, `⚙️`, `⚠️`, `⏱`, `🛠️`, `🏷️`, `🗺️`, `✔`, `🇦🇺`).
     - `2 spaces`: East Asian Wide (`W`/`F`) characters and pictographs whose cursor advance in terminal fonts is 2 cells (`📹`, `🕒`, `🔴`, `🔵`, `🟡`, `🟢`, `⚪`, `🚗`, `🏠`, `⚡`, `🔌`, `🔋`, `💾`, `🔄`, `📍`, `💰`, `📅`, `📊`, `📋`, `📜`, `🏢`, `🌐`, `🔍`, `🔎`, `🔒`, `🔗`, `🚀`, `🚪`, `📦`, `📥`, `⏰`, `⏳`, `✨`, `⭐`, `💡`, `🎉`, `🎯`, `📁`, `📂`, `📄`, `📮`, `📲`, `✅`, `❌`, `❓`).
   - **Column & Row Width Sanity Checks**:
     - `pad_display()` automatically validates that `display_len(result) == target_width`. If an emoji or multibyte boundary produces an unexpected width, it self-corrects whitespace to guarantee exact visual alignment.
     - `format_row()` sanity checks both each individual column width and the entire row width against `sum(col_widths) + len(col_widths) + 1` before rendering.
     - `format_title_line()` verifies and balances table title banners against `total_inner + 2`.

4. **Piped & Non-Interactive Execution Safety**:
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
| **REQ-010** | 2026-09-06 | Primary workspace transitioned to `$HOME/repos/tesla`; flag & explain iCloud additions before updating local | ✅ Complete | `AGENTS.md` |
| **REQ-011** | 2026-09-06 | Symlink iCloud path back to local repo; add commit approval, zero-PII, and token protection directives | ✅ Complete | `AGENTS.md`, `.gitignore` |
| **REQ-012** | 2026-09-06 | Zero-PII sanitization of `AGENTS.md` (replace hardcoded user paths with `$HOME`, `~`, or `$USER`) | ✅ Complete | `AGENTS.md` |
| **REQ-013** | 2026-09-06 | Redesign footage column into 2-tier header with 3 mini columns (`Saved`, `Sentry`, `Recent`) and emoji ticks | ✅ Complete | `Tools/tessie_drives_analyzer.py` |
| **REQ-014** | 2026-09-06 | Establish unified `Tools/table_formatter.py` with emoji 1-vs-2 space handling and column/row width sanity checks across all tools | ✅ Complete | `Tools/table_formatter.py`, all `Tools/*.py` |
| **REQ-015** | 2026-09-06 | Enforce column alignment rules across all terminal tables: text/sentences left; text-or-numbers-with-units right (currency always `.2f`); plain numbers right | ✅ Complete | `Tools/find_plugshare_chargers.py`, `Tools/find_tesla_chargers.py`, `Tools/tessie_charging_analyzer.py`, `Tools/tessie_drives_analyzer.py` |
| **REQ-016** | 2026-09-06 | Default sort for charger explorers (no explicit `--sort`, no proximity ref) should be State, then Station Name alphabetical, not arbitrary API order | ✅ Complete | `Tools/find_plugshare_chargers.py`, `Tools/find_tesla_chargers.py` |
| **REQ-017** | 2026-09-06 | Remove all TESLADRIVE-sync code outside `tesla_sync.sh` (deprecated) - includes fixing a live PII-exposure bug where `places.json` (real home/school/work addresses) was actually copied to any mounted TESLADRIVE volume | ✅ Complete | `Tools/find_plugshare_chargers.py`, `Tools/tessie_charging_analyzer.py`, `Tools/tessie_drives_analyzer.py`, `Tools/tessie_places.py` |
| **REQ-018** | 2026-09-06 | Add `--refresh-prices` to `find_plugshare_chargers.py` to backfill pricing for stations discovered via bulk/region search (which returns no tariff data) that don't already have a saved rate | ✅ Complete (rate-limited by PlugShare on large bursts - see BUG-008) | `Tools/find_plugshare_chargers.py` |

| **REQ-019** | 2026-09-06 | Audit all README/doc files against the actual current codebase for correctness | ✅ Complete | `README.md`, `Tessie/README.md`, `dashcam/README.md`, `CONTRIBUTING.md`, `.github/workflows/ci.yml`, `Tools/tessie_charging_analyzer.py` |

---

## 5. Known Bugs & Issue Tracker

| Bug ID | Component | Description | Impact | Status / Resolution |
|---|---|---|---|---|
| **BUG-001** | Charging Analyzer | Exploren/PlugShare flat tariff rates defaulted to $0.00/kWh because extractor looked only for Supercharger tier keys. | High | **Resolved**: Updated `get_expected_tariff_rate` to extract flat and schedule rates across all provider schemas. |
| **BUG-002** | Charging Analyzer | Uneven spacing in `inspect_session` caused dollar figures and kWh amounts to misalign across rows. | Medium | **Resolved**: Enforced fixed-width label padding (`lw_fin = 27`, `lw_loss = 31`). |
| **BUG-003** | Drives Analyzer | `Notable Destinations` and `Route` columns had hardcoded widths (e.g. 36/48), causing long names to push border pipes out and ruin table borders. | High | **Resolved**: Dynamically precompute all rows and calculate column widths using `max(display_len) + 1`. |
| **BUG-004** | Drives Analyzer | Non-tty piped runs (`echo "q" \| ./script.py`) triggered automatic cascading down through all days into footage dumps. | Medium | **Resolved**: Removed hardcoded `if not sys.stdin.isatty():` drill-downs, allowing clean piped input and EOF termination. |
| **BUG-005** | Table Formatter | Characters where macOS libc `wcwidth` is 1 cell (e.g. `🗓️`, `🛡️`, `⚙️`, `⚠️`) were measured as width 2, causing under-padded spaces and misaligned right table borders (`│`). | High | **Resolved**: Implemented macOS libc `wcwidth` ctypes integration, explicit `REPO_EMOJI_WIDTHS` mapping, and in-built visual emoji audit table self-test. |
| **BUG-006** | PlugShare Registry | `PlugShareRegistry.find_existing_match()` matched purely on exact station-name key before ever checking PlugShare ID, so two genuinely different real chargers sharing a display name (e.g. two separate "Bunnings Gladesville" listings ~70m apart - a public 120kW Exploren DC unit at id 801149 and a patron-only 22kW AC wall outlet at id 1517863) silently overwrote each other under the same JSON key, corrupting the surviving entry's `tariffs`/`keywords` via the existing-price-preservation merge logic. | High | **Resolved**: `find_existing_match()` now requires PlugShare-ID agreement (or an unset ID on one side) before treating a name/proximity match as the same station; `add_or_update()` disambiguates the JSON key (e.g. appending the network name or PlugShare ID) instead of overwriting when a create would otherwise collide with an unrelated station's key. The corrupted live `Tessie/plugshare_chargers.json` entry was split back into two correct entries using the field values from the user's own live-scraped terminal output as ground truth. |
| **BUG-007** | `find_tesla_chargers.py` | `suburb_col_w` for the "Location / Suburb" column was floored at `max(max_suburb_len + 2, 14)` - 14 is narrower than the 18-cell header text itself, so whenever no suburb name in the result set was long enough to push the column past 14, the header overflowed its own cell and sat flush against the border with no padding. | Medium | **Resolved**: floor now also accounts for the header's own width (`max(display_len("Location / Suburb") + 2, ...)`), matching the rule in §2.1 above. Verified with a synthetic table of real suburb names from a live `--near Home` run. |
| **BUG-008** | PlugShare Client | `PlugShareClient._get()` swallowed every non-404 failure (including HTTP 429/403 rate-limit responses) into a bare `None`, indistinguishable from "not found". `--refresh-prices` making many rapid sequential detail requests appears to trip PlugShare's rate-limiting - manual one-at-a-time lookups (with natural human pauses between them) succeed where the batched refresh failed for most stations. | Medium | **Partially resolved**: `_get()` now records the real failure reason (`self.last_error`) and applies jittered backoff-retry; `--refresh-prices` surfaces that reason per station and backs off harder (5s+) after a likely rate-limit hit, warning the user when it's happening. This makes the failure mode visible and less aggressive, but cannot force PlugShare's own server-side throttling to allow more throughput - re-running later, or falling back to one-at-a-time `--plugshare <id>` lookups, remains the reliable path for stations a burst run couldn't get. |
| **BUG-009** | Docs | `README.md` and `Tessie/README.md` referenced two removed scripts (`tessie_analyzer.py`, `tessie_renamer.py`) throughout their Tools Overview and Quick Start sections; `Tessie/README.md` also had an unclosed code fence that broke rendering of everything from section 2 onward. `.github/workflows/ci.yml`'s CI job `chmod`'d and ran `--help` on those same two removed scripts, so CI would fail on every push/PR. Smaller issues: `dashcam/README.md` documented `--dryrun` (real flag is `--dry-run`); `CONTRIBUTING.md`'s local-verification command was missing the `Tools/` path prefix; `find_tesla_chargers.py`'s stale `--sync` example (the flag no longer exists on that tool); `tessie_charging_analyzer.py`'s `--sync` help text didn't say it's a no-op like the other three tools' already do. | High | **Resolved**: both READMEs rewritten against the actual current script list and argparse flags (verified every documented command's `--help` actually runs); CI job now chmods/`--help`-checks all five current Python tools and runs `py_compile` across `Tools/*.py`; the smaller doc/help-text issues fixed alongside. |

## 6. TODOs & Backlog

- [ ] **TODO-001**: Add automatic cache invalidation for places geocoding if `places.json` is updated during an active session.
- [ ] **TODO-002**: Provide an interactive CLI shortcut in `tessie_charging_analyzer.py` to batch-update mismatched tariff entries in `charges_master.csv`.
- [ ] **TODO-003**: Check and test automated linking between dashcam footage on mounted external drives and deep-dive drive telemetry timestamps.
- [ ] **TODO-004**: Add unit/smoke tests for table formatting and column width calculation to prevent future regression.
- [x] **TODO-005**: ~~`--refresh-prices` on `find_plugshare_chargers.py` could not be exercised against the live PlugShare API from the agent's sandbox...~~ **Done**: user ran it live - see BUG-008/TODO-006 for the result (mostly rate-limited, not a code defect).
- [ ] **TODO-006**: Confirmed by the user that `--refresh-prices` still leaves most stations unpriced in one pass (BUG-008) - almost certainly PlugShare-side rate-limiting of the request burst rather than a code defect (the refresh pipeline itself was verified correct with a mocked network layer). If this persists even with the added backoff, consider spacing a full refresh across multiple invocations (e.g. `--limit` slices) rather than one big burst.

---

## 7. Verification & Update Checklist for Agents

Before completing any prompt or task, every agent must perform the following checklist:
1. **Codebase Check**: Ensure all modified files run without syntax errors (`python3 -m py_compile <file>`).
2. **Table Check**: If terminal tables were modified, verify borders with sample piped input or interactive test.
3. **Sync Check**: Verify symlink from iCloud path points to local `$HOME/repos/tesla/`.
4. **Docs Update**: If a request was fulfilled or a new bug/TODO was discovered, update Sections 4, 5, or 6 of this `AGENTS.md` file.
