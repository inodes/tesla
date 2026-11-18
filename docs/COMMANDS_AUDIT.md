# Commands: structure and plan

How commands are organised and how the app calls the work. The history of individual changes is in git; this file holds the rules and the plan.

## Layout

- Every command-line script is in `Tools/commands/` and is run through one entry point, `./Tools/tesla`. Run it with no arguments to list the commands by group (sync, analyse, report, build, teslacam, chargers, places); `./Tools/tesla <group> <command> -h` shows a command's own help. An old script name still works: `./Tools/tesla sync_api_tessie.py --drives`.
- A new command goes in `Tools/commands/` and in the GROUPS table in `Tools/tesla`.
- Shared code is in `Tools/lib/`, pluggable adapters in `Tools/providers/`, tests in `tests/`. One-off fixes, migrations and finished investigations are in `Tools/maintenance/`; retired scripts are in `Tools/archive/` with the reason in its `archive_log.md`.
- `Tools/` itself holds only `tesla`, `tesla_app.py`, `build_mac_app.sh`, `restart_app.command`, `fix_exec_bits.sh` and these folders.

## Rules for changes

- New commands are thin wrappers over `lib/` code: parse options, call the task, print progress.
- New app features call `lib/` code in-process and never add a script.
- Do not rename or delete a command outside the plan below; when you do, update everything that points at it (README, command lists, shell wrappers, `config.json` references) in the same change.
- Every command has man-page help (see `AGENTS.md`), and the app shows actions in plain words, never script names.

## How the app calls the work

1. **Work lives in `lib/` as named tasks**, such as `sync_alerts`, `sync_drives`, `build_drive_pages` and `make_teslacam_stills`. A task has an id, a plain-words title, an area, typed options with defaults from `config.json`, and a function that reports progress and checks a stop flag.
2. **A command-line script is a thin wrapper** over one task. The man-page help is generated from the task's description and options, so help and app options cannot drift apart.
3. **The app calls the same task in-process** through one runner and one set of endpoints: list tasks, start one, poll progress and output, stop it, read the last result.
4. **The user sees actions, not scripts.** "Sync alerts", "Rebuild drive pages" and "Take stills of new footage" appear where the data is (Alerts, Drives and TeslaCam tabs) and in one Activity panel showing what is running, progress, result and any error text (selectable). Output from a run is kept as a plain log.
5. **Long jobs keep working when the tab closes.** The runner refuses to start two jobs that write the same data at once. Jobs that read a TeslaCam drive say so, because they keep the drive busy until it is released.

## Plan

Each step is its own small commit, and nothing is deleted until its replacement is verified.

| Step | State | Notes |
|---|---|---|
| 1. Agree the plan | Done | |
| 2. Task registry and runner in the app, one Activity panel | Not started | The app still has three runners: the charger sync, the queue and the review jobs. |
| 3. Merge duplicate scripts, move shared code and tests, add `maintenance/` | Done | Duplicates are archived, shared code is in `lib/`, one-offs are in `maintenance/`. |
| 4. Rename commands to `verb_domain_thing` with stubs | Dropped | Replaced by `./Tools/tesla <group> <command>`; commands keep their file names and no stubs exist. |
| 5. Convert each domain's sync and reports to tasks, one domain per commit | Not started | Start with alerts, drives and TeslaCam. |
| 6. Man-page help for every script, generated from the task definitions | Ongoing | `-h` output must follow the man-page layout in `AGENTS.md`. |
