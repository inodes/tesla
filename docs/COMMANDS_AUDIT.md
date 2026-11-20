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
- 2026-11-20: The Keeper (`app_keeper.py`) names the drive it is working on ("Reading the telemetry on JOWUA (then LaCie)") instead of listing every connected drive, reads the events only of drives that changed since the last pass, and marks busy (orange dot) only the drives its stage has work on (`/api/keeper` `drive`, `drives`); the pictures stage reports the drive of each picture (`clip_keep.pictures_roles`). Test: tests/test_keeper_names_the_drive.py.
- 2026-11-20: Cameras are listed Front, Back, Left pillar, Left repeater, Right repeater, Right pillar in the video player's buttons (CAMN), the stills viewer and the event pictures (`camSort`). Test: tests/test_camera_order_ui.py.
- 2026-11-20: The note under an Update / pictures job said "Videos cannot play while this reads from the drive", which was not true (the page stops the playing videos when the job starts; the server still serves a video asked for later). It now says they were stopped and one started now is not blocked but may stutter. Test: tests/test_job_video_note_ui.py.
- 2026-11-20: Stills viewer: the signs over the still were whole frames (2896 px, kept as ..._f02.jpg) drawn as plain pictures, so the card showed the street, not the sign. They are cut out with `sgCut` from where the sign stands, as everywhere else. Test: tests/test_still_sign_cut_ui.py.
- 2026-11-20: TeslaCam list: two recordings of one park at Home (an ignored place) with a pause of up to an hour in the recording and no drive between them are one row (`run_cuts.home_hole`, used by `app_teslacam.hole_filled` and `clip_keep._runs`), so 00:00-08:15 and 08:30-08:31 on 20 Nov are one park. The keep bar has "Delete all Home parks" (every clip parked at Home, shown or not; the Select menu only ticked the ones shown, so a delete left the rest). Tests: tests/test_run_join.py, tests/test_home_delete_all_ui.py; tests/test_docs_paths.py lost two stale exceptions (tessie_timezone).
