# AGENTS.md — AI Agent Operating Instructions & Task Tracker

> **Notice to AI Agents**:
> This document is the primary instructions and standards reference for this repository - generic, shareable, and safe to commit (no personal specifics).
> **Every agent working in this repository MUST read this file upon startup, verify its accuracy against the current codebase, and keep it up-to-date after completing work.**
>
> The real, detailed project journal - Active Requests, Known Bugs, and TODOs, with real specifics - lives in `AGENTS.local.md` (gitignored, never committed). **Every agent MUST also read `AGENTS.local.md` on startup** for the actual current state of the project, and update it (not this file) as work progresses.

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
     - PII includes: Personal addresses, residential coordinates, trip/drive logs, tariff/billing rates tied to a real account, or any CSV files (all Tessie CSVs are classified as PII).
     - Non-PII includes: Public JSON files scraped from public websites (such as Tesla chargers or PlugShare registries) and sanitized `.example.json` templates.
     - Never commit `places.json`, `config.json`, or `Tessie/invoices/*.pdf`.
   - **PII Never Lives in the Repo Working Tree At All** (found via REQ-034 - `agl_tariff_rates.json`, real account tariff data, was being written directly into `Tools/`): this is stronger than "never commit" - a real/PII data file must never even be CREATED under `~/repos/tesla` (or its iCloud symlink), regardless of `.gitignore` status, because a file sitting there is one `git add -A` away from being committed and the whole point is to not depend on remembering to exclude it. Every script that reads/writes real data MUST resolve its path through that domain's own `resolve_*_data_home()` helper (e.g. `resolve_tessie_data_home()`, `resolve_agl_data_home()`) - NEVER a path built from `os.path.dirname(__file__)` or otherwise hardcoded beside the script. `.gitignore` entries for real files (`config.json`, `places.json`, etc.) are a defense-in-depth backstop only, not the primary control - if a real file is ever found inside the repo tree at all, that is itself the bug to fix (relocate the file AND fix the code path that put it there), not just an ignore-list gap to patch.
   - **Credential & Secret Protection**: If any string resembles an API token, password, or secret, flag it immediately for inclusion in `.gitignore`.
   - **Cadence**: Regular `git status` checks and tidy, logical git commits should occur.

3. **Terminal Execution & Permissions**:
   - Commands touching `~/Library/Mobile Documents/` or `/Volumes/TESLADRIVE` require sandbox bypass (`BypassSandbox: true` or equivalent direct shell permissions) due to macOS privacy and volume mount restrictions.
   - Do NOT use `cd` in tool calls; always specify full absolute working directories (`Cwd`).

4. **Continuous Maintenance of this Document**:
   - Check the **Active Requests**, **TODOs**, and **Known Bugs** sections when starting any task.
   - Update the status of completed items and add newly identified requests, bugs, or architectural decisions before concluding.

5. **CLI Help Text Stays User-Facing (No TODO/REQ/BUG/AGENTS.md References)** (added 2026-09-08, REQ-045):
   - A script's own `--help`/argparse `help=` text is what a real user reads - it must NEVER reference this document's TODO-XXX/REQ-XXX/BUG-XXX IDs or say "AGENTS.md". Put that context in code comments/docstrings instead, which agents read but `--help` never surfaces. This rule applies to every script in the repo, not just the one it was first caught in.

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
├── AGENTS.md                  # This document (generic AI instructions & standards - committed)
├── AGENTS.local.md            # Real project journal: Active Requests, Bugs, TODOs (gitignored, never committed)
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

## 7. Verification & Update Checklist for Agents

Before completing any prompt or task, every agent must perform the following checklist:
1. **Codebase Check**: Ensure all modified files run without syntax errors (`python3 -m py_compile <file>`).
2. **Table Check**: If terminal tables were modified, verify borders with sample piped input or interactive test.
3. **Sync Check**: Verify symlink from iCloud path points to local `$HOME/repos/tesla/`.
4. **Docs Update**: If a request was fulfilled or a new bug/TODO was discovered, update the Active Requests / Known Bugs / TODOs sections of `AGENTS.local.md` (not this file - this file stays generic and PII-free).
