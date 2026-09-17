# AGENTS.md — AI Agent Operating Instructions & Task Tracker

> **Notice to AI Agents**:
> This document is the primary instructions and standards reference for this repository - generic, shareable, and safe to commit (no personal specifics).
> **Every agent working in this repository MUST read this file upon startup, verify its accuracy against the current codebase, and keep it up-to-date after completing work.**
>
> The real, detailed project journal - Active Requests, Known Bugs, and TODOs, with real specifics - lives in `AGENTS.local.md` (gitignored, never committed). **Every agent MUST also read `AGENTS.local.md` on startup** for the actual current state of the project, and update it (not this file) as work progresses.

---

## 1. Core Operating Directives

0. **Implementation Plans First — Explicit Permission Required Before Action**:
   - **CRITICAL DIRECTIVE**: The agent MUST ALWAYS research, formulate, and present a comprehensive implementation plan first before taking any modifying actions.
   - **NEVER ACT PREMATURELY**: The agent MUST NOT execute source code edits, file creations, file renames (`git mv`), deletions, migrations, or modifying scripts until the user has reviewed the plan and explicitly given permission to proceed.
   - **Plan First, Act Only When Approved**: When a user asks for a feature, refactor, migration, or cleanup, provide the architectural breakdown, trade-offs, and step-by-step plan for confirmation first.

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
   - **PII Never Lives in the Repo Working Tree At All** (found via REQ-034 - `agl_tariff_rates.json`, real account tariff data, was being written directly into `Tools/`): this is stronger than "never commit" - a real/PII data file must never even be CREATED under `~/repos/tesla` (or its iCloud symlink), regardless of `.gitignore` status, because a file sitting there is one `git add -A` away from being committed and the whole point is to not depend on remembering to exclude it. Every script that reads/writes real data MUST resolve its path through that domain's own `resolve_*_data_home()` helper (e.g. `resolve_tessie_data_home()`, or a `BasePowerProvider.resolve_data_home()` implementation - REQ-070 generalized the AGL-specific `resolve_agl_data_home()` this rule originally named into that interface) - NEVER a path built from `os.path.dirname(__file__)` or otherwise hardcoded beside the script. `.gitignore` entries for real files (`config.json`, `places.json`, etc.) are a defense-in-depth backstop only, not the primary control - if a real file is ever found inside the repo tree at all, that is itself the bug to fix (relocate the file AND fix the code path that put it there), not just an ignore-list gap to patch.
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

All terminal tools (such as `Tools/analyse_drives.py` and `Tools/analyse_charging.py`) render rich Unicode box-drawing tables. The following rules are mandatory for all table implementations:

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
├── config.example.json        # Sanitized template for config.json (committed)
├── config.json                 # TODO-034 (2026-09-15): moved here from Tessie/ - this project outgrew
│                                #   being "a Tessie connector" a long time ago (AGL/PVOutput/dashcam/
│                                #   charger-discovery all live here too), so its one config file no
│                                #   longer sits inside the subdirectory named after the original,
│                                #   narrower scope. Real file, gitignored (see 1's PII rule); root
│                                #   config.json is checked first, Tessie/config.json only as a legacy
│                                #   fallback (lib/tessie_api_common.py's resolve_config_path()).
├── AGL/                        # Sanitized AGL tariff-rate example template (real file lives outside the repo)
├── Tessie/                    # Tessie telemetry, charging, and trip exports
│   ├── drives/                # Drive summary files & drives_master.csv
│   │   └── archive/           # Raw imported drive files (timestamp-archived)
│   ├── charges/               # Charging session summary files & charges_master.csv
│   │   └── archive/           # Raw imported charge files (timestamp-archived)
│   ├── invoices/              # Tesla Supercharger tax invoices (PDFs)
│   └── places.json            # Tagged places, boundaries, and GPS coordinates
├── Tools/                     # CLI entry points ONLY - every *.py here is a real, user-invoked script
│   │                          # with its own argparse-based --help (see 3.1); every *.sh here is a real,
│   │                          # user-invoked script with its own -h/--help guard (see 3.3). Nothing in
│   │                          # this directory is imported BY another script in this directory - if it
│   │                          # is, it belongs in lib/ or providers/ instead (see 3.1).
│   │
│   ├── lib/                   # Shared library modules (3.1) - plain function/helper collections that
│   │   │                      # one or more CLI scripts import. No argparse, no user-facing --help
│   │   │                      # (table_formatter.py's __main__ is a self-test, not a CLI). Package-
│   │   │                      # relative imports internally (`from .tessie_api_common import ...`);
│   │   │                      # `from lib.X import Y` from anywhere in Tools/ or Tools/providers_chargers/.
│   │   ├── __init__.py
│   │   ├── archive_naming.py    # Shared timestamp-archived filename scheme
│   │   ├── analysis_deepdive_charts.py   # Self-contained interactive HTML line-chart renderer
│   │   ├── evnex_common.py      # Evnex credential loading + client bootstrap
│   │   ├── pvoutput_common.py   # PVOutput config/auth/HTTP plumbing
│   │   ├── table_formatter.py   # Shared terminal table/box-drawing formatter
│   │   ├── tessie_api_common.py # Tessie API auth, config, and data-home path resolution
│   │   └── tessie_timezone.py   # Per-event real-timezone resolution helpers
│   │
│   ├── providers_chargers/     # Pluggable charging-network adapters (3.1) - one BaseChargerProvider
│   │   │                      # subclass per network, ALL implementing the same interface
│   │   │                      # (search_nearby/search_by_text/get_station_details/load_cached_registry),
│   │   │                      # swappable/interchangeable by design - this is what earns them a shared
│   │   │                      # `chargers_` prefix AND their own directory, unlike lib/'s grab-bag of
│   │   │                      # otherwise-unrelated helpers.
│   │   ├── __init__.py
│   │   ├── chargers_base.py    # BaseChargerProvider / UnifiedStationRecord + shared filter/dedup logic
│   │   ├── chargers_manager.py # ChargerDiscoveryManager - orchestrates all registered providers
│   │   └── chargers_tesla.py / chargers_chargefox.py / chargers_evx.py / chargers_evie.py /
│   │       chargers_bppulse.py / chargers_plugshare.py / chargers_ampcharge.py /
│   │       chargers_exploren.py / chargers_jolt.py / chargers_nrma.py   # one adapter per network
│   │
│   ├── providers_power/    # Pluggable power-retailer/distributor billing providers (3.1, REQ-070) -
│   │   │                      # one BasePowerProvider subclass per retailer, ALL implementing the same
│   │   │                      # interface (resolve_data_home/load_rate_periods/rate_for_date/
│   │   │                      # in_demand_window/solar_credit/demand_charge/...). Shaped differently
│   │   │                      # from providers_chargers/ above: only ONE power provider is ever active for a
│   │   │                      # household at a time (unlike chargers, queried many-at-once and merged),
│   │   │                      # so there's no "manager" here, just get_power_provider()'s factory - see
│   │   │                      # power_base.py's own docstring for the full reasoning.
│   │   ├── __init__.py
│   │   └── power_base.py / power_agl.py   # BasePowerProvider interface + the AGL implementation
│   │
│   ├── analyse_drives.py            # Drives explorer, footage linker, and master consolidator
│   ├── analyse_charging.py          # 3-way charging reconciler, tariff auditor, and invoice matcher
│   ├── places_manager.py            # Place tagging and geocoding utility
│   ├── sync_charger_all.sh          # One-command CHARGERS-domain sync (hardware/station registries)
│   ├── sync_charging_all.sh         # One-command CHARGING-SESSIONS-domain sync (Tessie/Evnex/invoices/AGL/cross-check prep)
│   ├── sync_drives_all.sh           # One-command DRIVES-domain sync
│   └── ...                          # every other CLI script - analyse_*.py, charger_*.py, power_*.py, sync_*.py, etc.
└── dashcam/                    # TeslaCam clips and sentry event utilities
```

### 3.1 Classifying a Python file: CLI script, library, or provider

Every `.py` file under `Tools/` is exactly one of these three things. Decide which BEFORE creating a new
file or moving an existing one - the answer decides where it lives and how it's named:

- **CLI entry point** (stays directly in `Tools/`): has its own `argparse.ArgumentParser` and a real
  `if __name__ == "__main__":` that a person runs directly (`./script.py ...`). Gets a comprehensive,
  man-page-style `-h`/`--help` (3.3) - non-negotiable, every one, always kept current.
- **Shared library module** (`Tools/lib/`): no argparse, no user-facing CLI - a collection of functions
  one or more CLI scripts (or other library modules) import. `table_formatter.py`'s `__main__` calling
  `run_self_test()` doesn't make it a CLI - a self-test isn't a user-facing command. If two or more
  otherwise-unrelated CLI scripts need the same logic, it belongs here, not copy-pasted or left sitting
  in whichever script happened to need it first.
- **Provider adapter** (`Tools/providers_chargers/`, `Tools/providers_power/`): specifically a subclass
  implementing one shared interface for one interchangeable implementation - `BaseChargerProvider` for a
  charging network, `BasePowerProvider` (REQ-070) for a power retailer/distributor. This is a narrower
  category than "library module" - it's reserved for genuinely pluggable, interchangeable implementations
  of one abstract interface, which is why the chargers family additionally shares the `chargers_` filename
  prefix (REQ-063) on top of living in its own directory (the power family's own CLI scripts share a
  `power_` prefix instead - see providers_power/power_base.py's own docstring for why that package isn't
  shaped identically to providers_chargers/). A shared helper that ISN'T an interchangeable adapter
  (`table_formatter.py`, `analysis_deepdive_charts.py`) is a library module, not a provider, even when the
  charging or power domain is what it's used for.

A file that is BOTH a real CLI script AND imported by other scripts for its functions (e.g.
`power_demand_calculator.py`, imported by `power_cost_calculator.py`) stays in `Tools/` as a CLI script -
the CLI-entry-point test is "does it have its own argparse + `__main__`", not "is it ever imported".

### 3.2 Naming & grouping convention

Prefer a shared **prefix** over a shared suffix for a family of related files, so they group together in a
plain alphabetical listing (`ls Tools/chargers_*`, `ls Tools/lib/`) without needing `ls | grep`. `chargers_`
(REQ-063) is the existing precedent for the providers_chargers/ family. When starting a new file that clearly belongs
to an existing family (another chart renderer, another sync wrapper, another provider), prefix it to match
rather than inventing a new pattern - and if a naming decision is genuinely ambiguous (which family it
belongs to, or whether a NEW prefix is warranted), settle it explicitly with the user before renaming
existing files, the same way REQ-063 settled "charger(s)/charging/charges" before touching anything (see
AGENTS.local.md for that precedent) - a repo-wide rename is expensive to undo and easy to get wrong on a
guess.

### 3.3 Every CLI script and every shell script gets a real `-h`/`--help`

Standing rule (BUG-074/BUG-080/BUG-084): every `.py` CLI entry point and every `.sh` script in this repo
must support `-h`/`--help` with comprehensive, man-page-style output (NAME/SYNOPSIS/DESCRIPTION/grouped
OPTIONS/EXAMPLES for `.py` via `argparse`; a `print_help`-then-`exit 0` guard checked BEFORE any real step
runs, for `.sh`) - and it must be kept up to date as flags/behavior change, not written once and left to
drift. `--help` must never have a side effect - BUG-084 found three `.sh` scripts where `--help` was
silently ignored and the ENTIRE real sync ran instead; that class of bug is why this is a written rule, not
just a habit.

---

## 7. Verification & Update Checklist for Agents

Before completing any prompt or task, every agent must perform the following checklist:
1. **Codebase Check**: Ensure all modified files run without syntax errors (`python3 -m py_compile <file>`).
2. **Table Check**: If terminal tables were modified, verify borders with sample piped input or interactive test.
3. **Sync Check**: Verify symlink from iCloud path points to local `$HOME/repos/tesla/`.
4. **Docs Update**: If a request was fulfilled or a new bug/TODO was discovered, update the Active Requests / Known Bugs / TODOs sections of `AGENTS.local.md` (not this file - this file stays generic and PII-free).
5. **Insertion-Boundary Check** (added 2026-09-16, after a real incident): `python3 -m py_compile` proves a file parses, nothing more - it cannot catch a splice that silently deletes the last line (often a `return`) of the function immediately BEFORE or AFTER a new block you inserted. When adding a new top-level function/class between two existing ones, re-read the few lines directly above and below the insertion point in the actual diff, not just the lines you added, to confirm the surrounding code is still intact.
6. **Git Hygiene Check**: Before considering a session that touched git done, run `git status` and actually read its output (not just its exit code). A crashed or interrupted git command can leave `.git/index.lock` (or another lock file) behind, which silently blocks every future `git add`/`git commit` - yours in a later session, or the next agent's - until someone notices and clears it; git itself will often warn about this on a plain `git status`.
