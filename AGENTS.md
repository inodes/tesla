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
     - Never commit `places.json`, `config.json`, or invoice PDFs (`*.pdf`).
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

6. **Full-Repo Reference Sweep Before Archiving, Moving, or Renaming Any File** (added 2026-09-21, BUG-120):
   - Moving a file to `Tools/archive/` (or renaming/relocating it anywhere) is not done until every OTHER
     file that imports it, subprocess-calls it, or invokes it by its old path has been found and fixed -
     `grep -rn "<old_name_without_ext>" --include=*.py --include=*.sh --include=*.md .` across the WHOLE
     repo, not just the one entry point you happen to be testing from. REQ-089's 7-file rename did this
     rigorously (a full 27-file reference audit, functional vs. cosmetic references separated and both
     fixed); this week's archiving of `charger_find_tesla.py` did not, and silently broke
     `sync_charger_all.sh` (aborted the entire 11-step sync on line 1) and `chargers_manager.py`'s live
     Tesla re-verify - neither surfaced by `py_compile`, only by someone actually invoking them. Test the
     actual entry points that reach the moved code (not just the one you moved it FOR) before calling an
     archive/rename complete.

7. **Check for ID Collisions Before Assigning a New REQ-/BUG-/TODO- Number** (added 2026-09-21):
   - Before writing a new `REQ-NNN`/`BUG-NNN`/`TODO-NNN` row in `AGENTS.local.md`, grep for that exact ID
     first (`grep -n "TODO-038" AGENTS.local.md`, etc.) to confirm it isn't already taken - a real
     collision happened (two unrelated sessions both wrote `TODO-038` for the same feature, one entirely
     unaware of the other) because neither checked. Separately: an agent's own commit-message bug
     numbering (Antigravity's informal "BUG-068", "BUG-069" style references in commit messages this week)
     MUST correspond to a real row in this file's own tracker, not a private, un-journaled numbering
     scheme - if a bug doesn't have a row here, it isn't tracked, no matter what a commit message calls it.

8. **Cross-Agent Handover Notes Go Into `AGENTS.local.md`'s Trackers, Not a New Standalone Document**
   (added 2026-09-21): Writing a narrative "what I did this week" document (`ANTIGRAVITY_FEEDBACK.md`,
   `CLAUDE_FEEDBACK.md`) is fine as a readable summary, but any OPEN action item in one must also get a
   real `REQ-`/`BUG-`/`TODO-` row in `AGENTS.local.md` - that's the one place status is tracked for both
   agents (see `AGENTS.local.md` Section 0). A standalone feedback doc with its own open items that never
   make it into the real tracker is exactly how work gets silently dropped between agents/sessions.

9. **"Verified" Means You Ran It and Are Reporting Real Output - Say So Explicitly When You Didn't**
   (added 2026-09-21): A status of "✅ Complete" / "verified" / a specific accuracy figure (e.g. "100%
   extraction accuracy across 38 invoices") must be backed by an actual command run against real data,
   with its real output referenced concretely - not a description of what the code is intended to do.
   Two real incidents this week: `CLAUDE_FEEDBACK.md` cited a `supercharge.info` API endpoint
   (`/allSiteChanges`) that returns a live 404 and doesn't match the code, which calls a different URL;
   and TODO-012/REQ-092/093 claimed verified 100% AGL invoice extraction accuracy when a live re-run
   against the real PDFs on disk showed 60% silently returning null. If something is implemented but
   genuinely not yet run against real data, say exactly that ("implemented, not yet tested live") rather
   than "verified" - `py_compile` passing, or the code merely existing, is not verification.

10. **Australian State and Territory Boundaries Come from the ABS** (added 2026-11-02): which state or territory a point (or an address) is in is decided by the boundaries the Australian Government prescribes, published by the Australian Bureau of Statistics in the Australian Statistical Geography Standard (ASGS Edition 3, July 2021 - June 2026), Digital Boundary Files: <https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files>.
   Use those boundaries (or data derived from them) for state, territory and time-zone decisions; do not guess a state from a street or place name (a street can be named after another state) or from rough latitude/longitude boxes.

---

## 2. Table Display & UI Formatting Standards

Command naming, structure and the app's task runner follow `docs/COMMANDS_AUDIT.md`; read it before adding, renaming or removing a script, and update its Progress section in the same change.

App UI (Tesellate) rules - functional wording only, progress on anything that waits, controls only when they apply, one fact once, Mac conventions - are in `docs/ui-guidelines.md`; read it before changing a page.

All terminal tools (such as `Tools/commands/analyse_drives.py` and `Tools/commands/analyse_charging.py`) render rich Unicode box-drawing tables. The following rules are mandatory for all table implementations:

1. **Dynamic Column Width Measurement (No Hardcoded Cell Sizing)**:
   - Never hardcode column widths for dynamic text columns (e.g., `Notable Destinations`, `Route`, `Station / Location`, `Address`).
   - Always precompute or inspect all rows in the dataset to calculate `w_col = max(len(" Header "), max(display_len(cell) for cell in rows) + 1)`.
   - Ensure `total_inner` accommodates the full header title (`title_str`). If `display_len(title_str) + 2 > total_inner`, expand the widest column to balance the table.

2. **Visual Width vs. String Length (Unicode & Emoji Handling)**:
   - Standard Python `len()` counts UTF-8 code points, which breaks alignment when emojis (e.g., 🏠, 🕒, 🛡️, 📹, ⚡, 🔴) or wide Asian characters are displayed.
   - Always calculate visual widths using `display_len()` and format cells with `pad_display(text, target_width, align="left"|"right"|"center")`.

3. **Centralized Table & Display Standards (`Tools/lib/table_formatter.py`)**:
   - All tools MUST import formatting utilities from `Tools/lib/table_formatter.py` (`char_width`, `display_len`, `pad_display`, `truncate_display`, `format_row`, `format_title_line`, `format_box_line`).
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

**Commands (2026-10-31):** every command-line script lives in `Tools/commands/` and is run through one entry point, `Tools/tesla`
(`./Tools/tesla` lists them by group: sync, analyse, report, build, teslacam, chargers, places; `./Tools/tesla sync drives`,
`./Tools/tesla analyse charges --inspect 142`; an old script name still works: `./Tools/tesla sync_api_tessie.py --drives`).
A new command is added to `Tools/commands/` AND to the GROUPS table in `Tools/tesla`. One-off tools go to `Tools/maintenance/`.
`Tools/` itself holds only `tesla`, `tesla_app.py`, `build_mac_app.sh`, `restart_app.command`, `fix_exec_bits.sh` and the folders.

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
├── Solar/                      # REQ-150 (2026-09-27): sanitized solar-install/panel-geometry example
│                              #   template (real file lives outside the repo, alongside AGL/PVOutput/
│                              #   SolarEdge - moved out of config.json's old 'solar' block, which had
│                              #   grown too large for a secrets-bearing settings file)
├── resources/                  # TODO-035 (2026-09-25): renamed from Tessie/ - sanitized *.example.json
│                              #   templates, API docs (TESSIE_API.md, PVOUTPUT_API.md), and other
│                              #   committed reference material only. Real per-domain DATA does not
│                              #   live under here, or under any repo-relative "Tessie/" path anymore.
│                              #   The real data directory (config.json's directories.data, outside
│                              #   this repo entirely) is its own flat, domain-named siblings, one
│                              #   per data source - drives/, charges/, chargers/, battery/, ... for
│                              #   Tesla/Tessie vehicle telemetry (the ONE vehicle data source today -
│                              #   no providers/vehicle/ package exists because nothing yet needs it
│                              #   pluggable); AGL/ for billing/tariff data; Solar/ for the physical
│                              #   panel-install geometry/ROI metadata (REQ-150); PVOutput/, SolarEdge/ -
│                              #   one folder per PV-monitoring provider that has ever been active
│                              #   (providers/pv/, 3.1); archive/ shared across domains.
├── Tools/                     # tesla (the one entry point), tesla_app.py, build_mac_app.sh,
│   │                          # restart_app.command, fix_exec_bits.sh and the folders below. Every
│   │                          # command script is in commands/, with its own argparse-based --help
│   │                          # (see 3.1) or -h/--help guard for *.sh (see 3.3). Nothing imports from
│   │                          # commands/ - shared code belongs in lib/ or providers/ (see 3.1).
│   │
│   ├── lib/                   # Shared library modules (3.1) - plain function/helper collections that
│   │   │                      # one or more CLI scripts import. No argparse, no user-facing --help
│   │   │                      # (table_formatter.py's __main__ is a self-test, not a CLI). Package-
│   │   │                      # relative imports internally (`from .tessie_api_common import ...`);
│   │   │                      # `from lib.X import Y` from anywhere in Tools/ or Tools/providers/.
│   │   ├── __init__.py
│   │   ├── archive_naming.py    # Shared timestamp-archived filename scheme
│   │   ├── analysis_deepdive_charts.py   # Self-contained interactive HTML line-chart renderer
│   │   ├── battery_health.py    # Battery health history (Tessie battery CSVs + battery_health_log.json)
│   │   ├── car_efficiency.py    # Car mass, grade-corrected kWh/100 km, distance confidence
│   │   ├── car_figures.py       # The car's usable kWh, full range (battery health) and Wh/km (drives); None when unknown
│   │   ├── charging_analyzer.py # ChargingAnalyzer + TeslaInvoiceParser (analyse_charging is its CLI)
│   │   ├── dashcam_clips.py     # TeslaCam clip list, hour offsets, clip cache and telemetry timeline
│   │   ├── drive_footage.py     # One drive's master row, Tessie CSV, window and the footage covering it
│   │   ├── evnex_common.py      # Evnex credential loading + client bootstrap
│   │   ├── footage_actions.py   # Footage queue from drive pages: copy, flag for delete, save, purge
│   │   ├── fuel_common.py       # NSW FuelCheck CKAN harvesting, streaming XLSX parser, and price DB
│   │   ├── invoice_common.py    # Zero-dep Deflate & CMap/Identity-H PDF parser + multi-provider extractors
│   │   ├── places_common.py     # places.json, geocoding, distances, Overpass look-up (places_manager is its CLI)
│   │   ├── power_bill.py        # Power bill rebuilt from usage + rates; the real bills it is checked against
│   │   ├── power_demand.py      # Power rate periods and the demand charge
│   │   ├── pvoutput_common.py   # PVOutput config/auth/HTTP plumbing
│   │   ├── table_formatter.py   # Shared terminal table/box-drawing formatter
│   │   ├── tessie_api_common.py # Tessie API auth, config, and data-home path resolution
│   │   └── timezones.py         # Time zones: GPS to zone, UTC to local time with the right abbreviation
│   │
│   ├── providers/              # Pluggable provider adapters (3.1), one subpackage per domain. Imported as
│   │   │                      # `from providers.chargers import ...`, `from providers.power import ...`,
│   │   │                      # `from providers.pv import ...` (Tools/ on sys.path). The package's own
│   │   │                      # __init__.py imports nothing, so loading one domain never loads another.
│   │   ├── __init__.py
│   │   │
│   │   ├── chargers/          # Charging-network adapters (3.1) - one BaseChargerProvider subclass
│   │   │   │                  # per network, ALL implementing the same interface
│   │   │   │                  # (search_nearby/search_by_text/get_station_details/load_cached_registry),
│   │   │   │                  # swappable/interchangeable by design - this is what earns them a shared
│   │   │   │                  # `chargers_` prefix AND their own directory, unlike lib/'s grab-bag of
│   │   │   │                  # otherwise-unrelated helpers.
│   │   │   ├── __init__.py
│   │   │   ├── chargers_base.py    # BaseChargerProvider / UnifiedStationRecord + shared filter/dedup logic
│   │   │   ├── chargers_manager.py # ChargerDiscoveryManager - orchestrates all registered providers
│   │   │   └── chargers_tesla.py / chargers_chargefox.py / chargers_evx.py / chargers_evie.py /
│   │   │       chargers_bppulse.py / chargers_plugshare.py / chargers_ampcharge.py /
│   │   │       chargers_exploren.py / chargers_jolt.py / chargers_nrma.py   # one adapter per network
│   │   │
│   │   ├── power/             # Power-retailer/distributor billing providers (3.1, REQ-070) - one
│   │   │   │                  # BasePowerProvider subclass per retailer, ALL implementing the same
│   │   │   │                  # interface (resolve_data_home/load_rate_periods/rate_for_date/
│   │   │   │                  # in_demand_window/solar_credit/demand_charge/...). Shaped differently
│   │   │   │                  # from chargers/ above: only ONE power provider is ever active for a
│   │   │   │                  # household at a time (unlike chargers, queried many-at-once and merged),
│   │   │   │                  # so there's no "manager" here, just get_power_provider()'s factory - see
│   │   │   │                  # power_base.py's own docstring for the full reasoning.
│   │   │   ├── __init__.py
│   │   │   ├── agl_tariff_rates.example.json
│   │   │   └── power_base.py / power_agl.py   # BasePowerProvider interface + the AGL implementation
│   │   │
│   │   └── pv/                # PV-monitoring providers (3.1, REQ-127) - one BasePvProvider subclass
│   │       │                  # per monitoring account, ALL implementing the same interface
│   │       │                  # (resolve_credentials/install_date/fetch_daily_range/fetch_intraday_range).
│   │       │                  # Shaped like power/ above (one active provider at a time -
│   │       │                  # config.json's pv.provider, default "solaredge" - not queried many-at-
│   │       │                  # once like chargers), for the same reason: a household has one PV
│   │       │                  # monitoring account active at a time. PVOutput was the original access
│   │       │                  # path before this package existed; it turned out to be a relay of this
│   │       │                  # SAME SolarEdge account (confirmed 2026-09-25 - PVOutput's own
│   │       │                  # "Automatic Uploads" settings show SolarEdge as both its Primary and
│   │       │                  # Secondary upload device), not an independent measurement, so it added
│   │       │                  # an extra hop with no unique data - see AGENTS.local.md.
│   │       ├── __init__.py
│   │       └── pv_base.py / pv_solaredge.py   # BasePvProvider interface + the SolarEdge implementation
│   │
│   ├── commands/              # every user-run command script, run as ./Tools/tesla <group> <command>
│   │   ├── analyse_drives.py            # Drives explorer, footage linker, and master consolidator
│   │   ├── analyse_charging.py          # 3-way charging reconciler, tariff auditor, and invoice matcher
│   │   ├── places_manager.py            # Place tagging and geocoding utility
│   │   ├── sync_charger_all.sh          # One-command CHARGERS-domain sync (hardware/station registries)
│   │   ├── sync_charging_all.sh         # One-command CHARGING-SESSIONS-domain sync (Tessie/Evnex/invoices/AGL/cross-check prep)
│   │   ├── sync_drives_all.sh           # One-command DRIVES-domain sync
│   │   ├── sync_fuel_all.sh             # One-command FUEL-domain sync (NSW FuelCheck historical & live)
│   │   ├── sync_fuel_history.py         # NSW FuelCheck discovery, download, and SQLite aggregator CLI
│   │   ├── sync_invoices.py             # Multi-provider PDF invoice parser, renamer, and importer CLI
│   │   └── ...                          # every other command - analyse_*.py, charger_*.py, power_*.py, sync_*.py, etc.
│   ├── maintenance/           # one-off fixes, migrations and API checks, run by hand
│   └── archive/               # retired scripts (archive_log.md says why each was moved)
└── dashcam/                    # TeslaCam clips and sentry event utilities
```

### 3.1 Classifying a Python file: CLI script, library, or provider

Every `.py` file under `Tools/` is exactly one of these three things. Decide which BEFORE creating a new
file or moving an existing one - the answer decides where it lives and how it's named:

- **CLI entry point** (lives in `Tools/commands/`, listed in `Tools/tesla`'s GROUPS): has its own `argparse.ArgumentParser` and a real
  `if __name__ == "__main__":` that a person runs directly (`./script.py ...`). Gets a comprehensive,
  man-page-style `-h`/`--help` (3.3) - non-negotiable, every one, always kept current.
- **Shared library module** (`Tools/lib/`): no argparse, no user-facing CLI - a collection of functions
  one or more CLI scripts (or other library modules) import. `table_formatter.py`'s `__main__` calling
  `run_self_test()` doesn't make it a CLI - a self-test isn't a user-facing command. If two or more
  otherwise-unrelated CLI scripts need the same logic, it belongs here, not copy-pasted or left sitting
  in whichever script happened to need it first.
- **Provider adapter** (`Tools/providers/chargers/`, `Tools/providers/power/`, `Tools/providers/pv/`):
  specifically a subclass implementing one shared interface for one interchangeable implementation -
  `BaseChargerProvider` for a charging network, `BasePowerProvider` (REQ-070) for a power
  retailer/distributor, `BasePvProvider` (REQ-127) for a PV-monitoring account. This is a narrower
  category than "library module" - it's reserved for genuinely pluggable, interchangeable implementations
  of one abstract interface, which is why the chargers family additionally shares the `chargers_` filename
  prefix (REQ-063) on top of living in its own directory (the power and PV families' own CLI scripts
  share a `power_`/`pv_` prefix instead - see providers/power/power_base.py's own docstring for why those
  packages aren't shaped identically to providers/chargers/). A shared helper that ISN'T an interchangeable
  adapter (`table_formatter.py`, `analysis_deepdive_charts.py`) is a library module, not a provider, even
  when the charging/power/PV domain is what it's used for. This one-active-provider-at-a-time,
  config-key-selected pattern (`chargers_manager.py`'s many-at-once merge is the one exception, since
  charging networks are genuinely queried together, unlike power/PV) is this repo's settled answer to
  TODO-035's "separate data sources into distinct domains" ask - a new domain gets its own
  `providers/<domain>/` subpackage + its own `config.json` top-level section when (and only when) more than
  one real, interchangeable implementation exists for it. Vehicle telemetry (Tessie/Tesla) has no
  `providers/vehicle/` subpackage for exactly this reason: there's only ever been one data source, so there's
  nothing yet to make pluggable - adding one preemptively would be structure without a use.

A CLI script keeps only its own argparse, menus and output; any function or class another script, a lib
module, a provider or the app needs is moved into `Tools/lib/` and imported from there by every user,
the script itself included (2026-11-02: e.g. `power_demand_calculator.py`'s rate periods live in
`lib/power_demand.py`, `analyse_charging.py`'s ChargingAnalyzer in `lib/charging_analyzer.py`). Nothing in
`Tools/lib/`, `Tools/providers/` or `Tools/tesla_app.py` imports from `Tools/commands/`, and none of them
puts `Tools/commands/` on `sys.path`; a name that is both a lib module and a command
(`teslacam_ledger`, `teslacam_scan`, `teslacam_stills`) is imported as `from lib import teslacam_<x>`.
`tests/test_lib_no_commands.py` checks all three.

### 3.2 Naming & grouping convention

Prefer a shared **prefix** over a shared suffix for a family of related files, so they group together in a
plain alphabetical listing (`ls Tools/providers/chargers/chargers_*`, `ls Tools/lib/`) without needing `ls | grep`. `chargers_`
(REQ-063) is the existing precedent for the providers/chargers/ family. When starting a new file that clearly belongs
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

**REQUIREMENT - help output is a man page (user, 2026-09-30).** Source of truth: "Man Pages: The Complete
Guide" (https://medium.com/better-programming/man-pages-the-complete-guide-800ad93425fe). Every `-h`/`--help`
in this repo MUST follow it. Checklist - an agent adding or changing a script or flag checks every line:

1. **Sections, in this order, only those that apply.** Required: **NAME**, **SYNOPSIS**, **DESCRIPTION**.
   For commands (all our scripts are man section 1): **OPTIONS** and **EXIT STATUS**. As applicable:
   **ENVIRONMENT**, **FILES**, **NOTES**, **CAVEATS**, **BUGS**. Recommended: **EXAMPLES**, **SEE ALSO** (last).
   Never add an empty section. (Function-only sections - LIBRARY, RETURN VALUE, ERRORS, ATTRIBUTES - don't
   apply to our CLIs.)
2. **NAME** is exactly one line: `name - short one-line description` (like `ls - list directory contents`).
3. **SYNOPSIS contains no prose.** It only illustrates every way of calling the command, one line per form:
   **bold** = type exactly as shown; *italic/underlined* = replace with your value; `[ ]` = optional;
   `a | b` = choose one; `arg...` = repeatable.
4. **DESCRIPTION** covers the usual case and how the command uses files, stdin, stdout and stderr. It omits
   internals and implementation details unless they are critical to using it - no rsync maths, tombstone
   mechanics, TTY rules, ticket numbers (BUG-/REQ-/TODO-) or history; those go in code comments and
   AGENTS.local.md. Anything about a specific option goes in OPTIONS, not here.
5. **OPTIONS**: every flag, each on its own line (short and long form), with what it does / how it changes
   behaviour in one or two plain lines indented underneath. A flag needing a paragraph is a sign the detail
   belongs in DESCRIPTION, CAVEATS or a doc.
6. **EXIT STATUS** lists each exit value and when it happens. **ENVIRONMENT** lists every variable that
   changes behaviour and its effect. **FILES** gives full pathnames that are "both factual and actual" -
   files the script really reads or writes, nothing aspirational.
7. **SEE ALSO** is ordered by section number, then alphabetically by name.
8. **Layout**: headings flush left in capitals; body indented 7 spaces; option descriptions indented 14.
   Bold/underline via ANSI only when stdout is a terminal, plain text otherwise (so piping to a file or
   `less` stays clean).
9. **No side effects**: `-h` prints and exits 0 before any real step runs (the BUG-084 rule above).
10. **Kept current**: changing a flag or behaviour means updating the help in the same change.

Python scripts render this from their argparse parser via a shared helper (so SYNOPSIS/OPTIONS can't drift
from the real flags); each script supplies only NAME, DESCRIPTION, EXIT STATUS, FILES, EXAMPLES, SEE ALSO.
Reference implementation: `Tools/commands/sync_dashcam.sh -h`.

### 3.4 Interactive menus: one option per line

Standing rule (REQ-138, restated REQ-178): any prompt offering more than one choice prints each option on
its own line, then asks with a bare prompt (`> `). Never join options into one line with commas, slashes
or pipes - not in `input()` text, not in a "Commands:"/"Options:" line, not in an "Invalid choice" hint,
and not when the list is built dynamically (`", ".join(actions)`). A single yes/no confirmation
(`[y/N]`, `[Y/n]`) is fine on one line.

### 3.4.1 Long runs: one progress bar, no chatter

Standing rule: any command that loops over more than a handful of items (drives, clips, files, API fetches) uses
`Tools/lib/progress.py` (`Bar`): one line that updates in place with percent, count, speed, time left and what is being
done now. Per-item messages ("fetching...", "fetched...") are not printed; only what needs attention (an error, a skipped
item) is, through `bar.note()`. Redirected output gets a plain line every ~5 %. Do not print what is obvious: show a masked
VIN/token (`***686462`, `***xyz`), not "token not shown".

```
Action:
  [o]pen chart in browser
  [m]anually correct
  [Enter] return
  >
```

### 3.5 Script permissions: every .py and .sh is u+x

Standing rule (REQ-180): every `*.py` and `*.sh` in the repo - entry points, `lib/` modules, provider
modules, tests - must have the user-execute bit (`chmod u+x`), and git must record it (mode `100755`).

- A new script gets `chmod u+x <file>` straight after it is created.
- Edits that rewrite a file (write to a temp file, then `os.replace`) must copy the mode first
  (`shutil.copymode(old, tmp)`), as the atomic-edit pattern already does.
- An agent must never leave a script without u+x. An in-place rewrite (`open(path, "w")`) on the
  device mount has dropped the bit before (2026-10-14: `drive_report.py`, `dashcam_flip_review.py`,
  `sign_scan.py`), so run `chmod u+x <file>` after every edit, not just after creating a file.
- `Tools/fix_exec_bits.sh` repairs any missing bits (`--check` only reports). Run it after pulling,
  restoring from a backup or a cloud-synced folder, or whenever a script says "Permission denied".
- `.git/hooks/pre-commit` applies `u+x` to staged `.py`/`.sh` files and records it with
  `git update-index --chmod=+x`. Hooks are not versioned, so on a fresh clone run
  `Tools/fix_exec_bits.sh --install-hook`.


### 3.5a Settings live in config.json, not in code

Standing rule: a value a user might reasonably want to change (a threshold, a time limit, a server list, a map style, a window size, a port)
is a setting in `config.json`, never a literal buried in code. The app, report and places settings are the `"app"` block read by
`Tools/lib/app_config.py` (`app_config.get("drives.repark_max_km")`); every setting has a default there and is documented in
`config.example.json`, so `config.json` only needs the values being changed. The front end gets them from `/api/settings`. When you add one,
add it to `DEFAULTS` in `app_config.py` and to `config.example.json` in the same change. Secrets stay in `config.json` / the environment and are
never printed.

### 3.5.1 Removable drives: identify by volume UUID; keep only the Archive connected

Standing rule (2026-10-14; identity changed to the volume UUID 2026-10-21).

**Identity: never by mount name.** The volumes appear as `TESLADRIVE`, `TESLADRIVE 1`, `TESLADRIVE 2` in mount order, so
the names are interchangeable and say nothing, and the car formats every drive with the same name. A drive is identified by
its **volume UUID** (`diskutil info` calls it Volume UUID). The first time a drive is seen it is asked what it is for (car /
archive / copy) and given a one-word name; the answer is kept in `config.json` under `teslacam.drives` with a key letter
(role: A Archive, J JOWUA, U Tesla USB for the known drives). `Tools/lib/drive_registry.py` holds the registry (`lookup`,
`register`, `forget`, `mounted_volumes`, `mount_of`, `ranked`) and `Tools/commands/manage_teslacam_drives.py` lists and edits it.
A marker folder in the root (`ARCHIVE_2TB`, `JOWUA_1TB`, `TESLA_USB_*`) only registers a drive the first time its UUID can be
read; `Tools/lib/drive_roles.py` implements the lookups. A disk image mounted from a drive carries that drive's UUID and
marker, so it looks like a second copy of it: detach it before judging what is connected. Code must not hard-code
`/Volumes/TESLADRIVE...` paths to mean a particular drive, and queue/action files carry the drive's role, not its name
(`drive_actions.py` resolves the role at run time). The write-up of the JOWUA recovery is in the Tesla project
(`claude/jowua-recovery-2026-10-21.md`).

**Connection: only the Archive stays connected to an agent session.** A connected folder is a share served by the Claude
app's VM (`com.apple.Virtualization.VirtualMachine`); every file a script reads through it stays open on the Mac until the
folder is disconnected, so Finder cannot eject the volume ("in use"). A footage scan of JOWUA left ~5,900 open files.
- Scripts read the Archive only by default (`--with-secondary` or an explicit `--teslacam` opts JOWUA / the USB in).
- Only connect JOWUA or the USB for a task that needs them, and say when the folder can be disconnected again.
- Check on the Mac with `lsof "/Volumes/<name>"`; the fix is to disconnect the folder in the app or relaunch it.

### 3.6 Backups go in `.bak_archive/`, never beside the file

Standing rule (REQ-181, narrowed 2026-10-21): a file that is committed needs no copy, because git keeps its versions. Copy only
a file that git does not hold (git-ignored or never committed: `config.json`, local data, notes) before editing it, to `.bak_archive/` (the one inside
the folder being edited, e.g. `Tools/.bak_archive/`; repo-root files use `./.bak_archive/`) as
`<name>.bak-<ticket>[-<timestamp>]`. `.bak_archive/` is git-ignored. Never leave `*.bak*` files next to
the original - they clutter the folder, break shell tab-completion and sit in the same directory the
scripts run from. Data-folder backups (`charges_master.csv` etc.) go in the data `archive/` folder with
a name that does not contain "charges"/"drives", because the loaders read matching files there as data.

`config.json` is not in git, so `Tools/lib/config_backup.py` copies it to `.bak_archive/config.json.bak-auto-<UTC time>`
before the drive registry changes it and each time the app starts, unless the newest copy is identical. The newest 20 stay;
older ones are moved (not deleted) to `.bak_archive/config.json.older/` and logged in `.bak_archive/archive_log.md`.

### 3.7 Working method: nothing is deleted, claims are cited, gaps are stated

Standing rule (2026-10-21).

- **Never delete files.** To remove an obsolete file, move it to an archive folder (`Tools/archive/` for scripts,
  `.bak_archive/` for backups) and write the reason in that folder's `archive_log.md`. Only the user deletes, on their own machine.
- **Every factual statement about the repo or its documents names its source** as `[file > section]`, for example
  `[AGENTS.md > 3.6]`, taken from the file as it is now, not from memory. Open a long file only for the part that is needed.
- **If it is not in the files, say "Not found in documents"** and do not infer it. When two files disagree, say which two and
  what each says.
- **When a file is added, moved or removed, update what points at it** (README, this file, the command lists) in the same
  change; see the Reference Sweep Check in Section 7.

---

## 7. Verification & Update Checklist for Agents

Before completing any prompt or task, every agent must perform the following checklist:
1. **Codebase Check**: Ensure all modified files run without syntax errors (`python3 -m py_compile <file>`).
2. **Table Check**: If terminal tables were modified, verify borders with sample piped input or interactive test.
3. **Sync Check**: Verify symlink from iCloud path points to local `$HOME/repos/tesla/`.
4. **Docs Update**: If a request was fulfilled or a new bug/TODO was discovered, update the Active Requests / Known Bugs / TODOs sections of `AGENTS.local.md` (not this file - this file stays generic and PII-free).
5. **Insertion-Boundary Check** (added 2026-09-16, after a real incident): `python3 -m py_compile` proves a file parses, nothing more - it cannot catch a splice that silently deletes the last line (often a `return`) of the function immediately BEFORE or AFTER a new block you inserted. When adding a new top-level function/class between two existing ones, re-read the few lines directly above and below the insertion point in the actual diff, not just the lines you added, to confirm the surrounding code is still intact.
6. **Git Hygiene Check**: Before considering a session that touched git done, run `git status` and actually read its output (not just its exit code). A crashed or interrupted git command can leave `.git/index.lock` (or another lock file) behind, which silently blocks every future `git add`/`git commit` - yours in a later session, or the next agent's - until someone notices and clears it; git itself will often warn about this on a plain `git status`.
7. **Tests**: run `python3 -m unittest discover -s tests` from the repo root. With no real config or data (cloud sessions, CI, a fresh clone) use `TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest discover -s tests`. Tests must never read the real config, tariff files or data folder; mock them.
7. **Reference Sweep Check** (added 2026-09-21, BUG-120): If a file was moved, renamed, or archived this
   session, grep the WHOLE repo for its old name/path before calling the session done, and actually run
   (not just `py_compile`) at least one real entry point that depends on it - see Section 1 item 6.
8. **Claim-Honesty Check** (added 2026-09-21, BUG-122/BUG-126): Before writing "verified"/"complete"/a
   specific accuracy number anywhere (a tracker row, a handover doc, a commit message), confirm you
   actually ran it against real data and can point to the real output - see Section 1 item 9.

## 8. Cloud sessions: reporting back

The cloud sandbox has no `config.json` and no real data. The owner runs the same repo on his Mac, where both exist, and pastes what you ask for into a chat with the reviewing Claude. So:

1. Run everything that can run in the sandbox with `TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata`. Say plainly what that left untested ("implemented, not tested on real data").
2. End the PR description (and your final message) with a block headed **Paste back**, in this order:
   - **Done**: what changed, one line per change, with file names.
   - **Not run on real data**: each thing, and why.
   - **Run on your Mac** (from `~/repos/tesla`, branch checked out): a fenced code block of exact commands, each followed by a line saying what a good result looks like. Prefer commands that print short, self-checking output (counts, PASS/FAIL, a diff against the old behaviour) over long dumps. Use real data and `config.json` as they are: do not copy either into the repo or the PR.
   - **Risks**: anything a reviewer should look at first (data written, files moved or deleted, anything that touches money, dates or time zones).
3. Never print or ask for secrets, tokens, addresses or coordinates. If a command's output might contain them, tell the owner to trim it before pasting.
4. Never push to `merged`: push your branch and open a PR into it.
