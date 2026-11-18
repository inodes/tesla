# AGENTS.md - rules for AI agents in this repo

Cross-project rules (plan first, commit approval, dry-run, backups, PII, "verified") live in the user's own global instructions (their global `CLAUDE.md` and cross-project `AGENTS.md`), outside this repo. Follow them too; if you cannot see them, tell the user. This file holds only what is specific to this repo, and it is generic: no personal specifics, safe to commit.

The project journal (open requests, known bugs, TODOs) is `AGENTS.local.md`, which is git-ignored. Read it at the start of a task and update it, not this file, as work progresses.

Layout, module map and command list: `docs/ARCHITECTURE.md` and `./Tools/tesla` (lists every command by group).

## 1. Working rules

1. **Workspace**: `$HOME/repos/tesla`. Do not keep a copy of the repo in a cloud-synced folder.
2. **Plan first**: edits, renames, deletions and migrations need approval (global rule). Show the diff and the exact commit message before committing.
3. **No PII in the repo**: no addresses, coordinates, trip or drive logs, account tariff or billing data, or Tessie CSVs, in files, docs or commit messages. Use `$USER`, `$HOME` or `~`, never a real username. Never commit `config.json`, `places.json` or invoice PDFs. Public registry data and sanitised `*.example.json` templates are fine.
4. **Real data never lives in the working tree**, even when git-ignored. Resolve the path with the domain's `resolve_*_data_home()` helper; never build it from `__file__`. A real file found under the repo is a bug: move it and fix the code that put it there.
5. **Secrets**: anything that looks like a token or password goes in `.gitignore` and is never printed.
6. **Shell**: use absolute paths; do not `cd` in tool calls. Commands touching cloud-synced folders or removable drives need the sandbox bypass.
7. **Never delete files from disk.** Move retired scripts to `Tools/archive/` and backups to `.bak_archive/`, and say why in that folder's `archive_log.md`. Both folders are local and gitignored: git history already keeps old versions, so never commit an archive. Only the user deletes.
8. **Before moving, renaming or archiving a file**, grep the whole repo for its old name (`grep -rn "<name>" --include=*.py --include=*.sh --include=*.md .`), fix every reference, then run an entry point that reaches the moved code. `py_compile` does not catch broken references.
9. **"Verified" means you ran it** and can show the output. Otherwise write "implemented, not tested on real data".
10. **State and territory** come from the ABS ASGS boundaries (Digital Boundary Files), not from street names or lat/lon boxes.
11. **Facts about the repo cite their source** as `[file > section]`. If it is not in the files, say "Not found in documents".

## 2. Code structure

Every `.py` under `Tools/` is exactly one of:

- **Command** (`Tools/commands/`): has argparse and a `__main__`; run as `./Tools/tesla <group> <command>`. Add it to the GROUPS table in `Tools/tesla`. One-off tools go in `Tools/maintenance/`.
- **Library** (`Tools/lib/`): no argparse, imported by commands, providers or the app. Logic needed by two or more commands belongs here.
- **Provider** (`Tools/providers/<domain>/`): one subclass per interchangeable implementation of a base interface (chargers, power, pv). Create a new domain only when a second real implementation exists.

Nothing in `lib/`, `providers/` or `tesla_app.py` imports from `commands/`; `tests/test_lib_no_commands.py` checks it. A name that is both a lib module and a command is imported as `from lib import <name>`.

Naming: give related files a shared prefix so they sort together (`chargers_*`). If a naming or grouping decision is ambiguous, ask before renaming existing files.

Settings a user might change (thresholds, limits, ports, map styles) live in `config.json`, never as literals. Add a default in `Tools/lib/app_config.py` and an entry in `config.example.json` in the same change.

## 3. Command-line behaviour

- **Help**: every `.py` command and `.sh` script supports `-h`/`--help` as a man page (NAME, SYNOPSIS, DESCRIPTION, OPTIONS, EXIT STATUS, then as applicable ENVIRONMENT, FILES, EXAMPLES, SEE ALSO). It never has side effects, is updated with every flag change, and never mentions ticket IDs or `AGENTS.md`. Reference: `Tools/commands/sync_dashcam.sh -h`.
- **Menus**: one option per line, then a bare `> ` prompt. Only a single `[y/N]` confirmation may sit on one line.
- **Long runs**: use `Tools/lib/progress.py` (`Bar`): one in-place line, no per-item chatter, `bar.note()` for errors. Show masked secrets (`***xyz`), not "token not shown".
- **Tables**: use `Tools/lib/table_formatter.py` (`display_len`, `pad_display`, `format_row`). Never hardcode widths for dynamic columns, never use `len()` for visual width, and never auto-cascade menus when stdin is piped (catch `EOFError`).
- **Executable bit**: every `.py` and `.sh` is `u+x` and recorded as `100755`. Run `chmod u+x` after every edit, and `Tools/fix_exec_bits.sh` after a pull, restore or "Permission denied". On a fresh clone run `Tools/fix_exec_bits.sh --install-hook`. Atomic rewrites copy the mode first (`shutil.copymode`).

## 4. Commands, app and docs

- Command naming and structure follow `docs/COMMANDS_AUDIT.md`: read it before adding, renaming or removing a script and update its Plan table in the same change.
- App UI rules are in `docs/ui-guidelines.md`; read it (section 11 especially) before changing anything under `Tools/lib/app_ui/`.
- OSM and supercharge.info edits: `docs/OSM_EDITING.md`.
- When a file is added, moved or removed, update what points at it (README, command lists) in the same change.

## 5. Data and environment

- **Sandbox**: `api.plugshare.com` and `api.tessie.com` are blocked from the cloud VM, and `PlugShareClient._get()` returns `None` for any non-404 error. An empty result from the VM is not "not found"; have the user run it. General VM rules are in the user's global instructions.
- **Real-data tests from the VM**: pass an explicit override path (for example `consolidate_drives(master_dir=...)`); never call a no-override convenience such as `load_drives()`, `load_charges()` or a bare `--consolidate`.
- **Providers**: match on provider/network ID before merging records; distinct providers can share an address. Keep multi-tier records at co-located sites.
- **Archive loaders**: archives hold lists of snapshots. Normalise list values; do not skip them.
- **Removable drives** are identified by volume UUID, never by mount name or a hard-coded `/Volumes/...` path. The registry is `Tools/lib/drive_registry.py` (edited with `manage_teslacam_drives.py`). Keep only the Archive drive connected to an agent session: an open folder share stops Finder ejecting a volume. Scripts read the Archive by default; opt in to others with `--with-secondary` or `--teslacam`.
- **Backups**: git holds committed files, so copy only files git does not hold (`config.json`, local data, notes), into the `.bak_archive/` of the folder being edited as `<name>.bak-<ticket>[-<timestamp>]`. Never leave `*.bak*` beside the original. Data-folder backups go in the data `archive/` under a name that does not contain "charges" or "drives".

## 6. Before you finish

1. `python3 -m py_compile` on every changed file.
2. Tests: `python3 -m unittest discover -s tests`. With no real config or data use `TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest discover -s tests`. Tests never read real config or data.
3. If you inserted a block, read the lines just before and after it; a splice can silently delete a `return`.
4. Terminal tables changed: check borders with piped and interactive input.
5. `git status`, and read it. Remove a stale `.git/index.lock` left by an interrupted command.
6. Moved or renamed files: reference sweep (rule 8).
7. Update `AGENTS.local.md` for finished or newly found items.

## 7. Cloud sessions: reporting back

The cloud sandbox has no `config.json` and no real data. Run everything that can run there with `TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata`, and say plainly what stayed untested. End the PR description and your final message with a **Paste back** block:

- **Done**: one line per change, with file names.
- **Not run on real data**: each item and why.
- **Run on your Mac** (from `~/repos/tesla`, branch checked out): a fenced block of exact commands, each followed by what a good result looks like. Prefer short self-checking output (counts, PASS/FAIL, a diff against old behaviour).
- **Risks**: anything a reviewer should look at first (data written, files moved or deleted, money, dates, time zones).

Never print or ask for secrets, tokens, addresses or coordinates; tell the owner to trim output that may hold them. Never push to `merged`: push your branch and open a PR into it.
