# Commands audit and plan

Audit of `Tools/` as of 2026-10-19: 79 top-level scripts. Decisions so far: names follow **verb_domain_thing**; the app has its **own, transparent way to call the work** (the user never sees or chooses a script).

## What I found

- **13 of 79** have man-page style help. **42** are referred to by the app in some way; the rest are command-line only.
- **Eight pairs look like duplicates** (same description, two names). Each pair needs a `diff` before one is removed:
  `tessie_api_sync.py` / `sync_api_tessie.py`, `tessie_charging_analyzer.py` / `analyse_charging.py`, `tessie_drives_analyzer.py` / `analyse_drives.py`, `tessie_places.py` / `places_manager.py`, `evnex_api_sync.py` / `sync_api_evnex.py`, `chargers_sync_all.sh` / `sync_charger_all.sh`, `charging_sync_all.sh` / `sync_charging_all.sh`, `drives_sync_all.sh` / `sync_drives_all.sh`.
  Also `Tools/tessie_timezone.py` / `Tools/lib/tessie_timezone.py`, and `evnex_explore.py` also lives in `archive/`.
- **Shared code sits among the commands** (not commands): `agl_common`, `evnex_common`, `pvoutput_common`, `tessie_api_common`, `tessie_timezone`, `table_formatter`, `archive_naming`.
- **Tests sit among the commands**: `test_pvoutput_api.py`, `test_tessie_api.py` (plus `archive/`).
- **One-off fixes and migrations** that should not look like everyday commands: `fix_drive_zone_labels`, `fix_exec_bits.sh`, `frac_fill`, `migrate_charge_folders`, `migrate_drive_folders`, `migrate_old_snapshots`, `build_mac_app.sh`, `home_charge_deep_dive`.
- **Naming mixes at least five styles**: `sync_x_all`, `x_sync_all`, `analyse_x`, `tessie_x_analyzer`, `x_report`, `x_find`.
- **The app calls work in three different ways**: one runner for the charger sync (`app_sync.py` runs a shell script), a queue runner (`/api/queue/run`), and separate review jobs. None share progress, stop or log handling.

## Names (verb_domain_thing)

| Now | Proposed | Notes |
|---|---|---|
| sync_drives_all.sh (and drives_sync_all.sh) | sync_drives.sh | one per domain |
| sync_charging_all.sh (and charging_sync_all.sh) | sync_charges.sh | |
| sync_charger_all.sh (and chargers_sync_all.sh) | sync_chargers.sh | |
| sync_fuel_all.sh, sync_fuel_history.py | sync_fuel.sh, sync_fuel_prices.py | |
| sync_api_tessie.py (and tessie_api_sync.py) | sync_tessie.py | flags stay: drives, charges, alerts, idles, tire pressure, battery |
| sync_api_evnex.py (and evnex_api_sync.py) | sync_evnex.py | |
| sync_api_abrp.py | sync_abrp.py | |
| sync_power_usage.py, agl_usage_sync.py, sync_agl_rates.py | sync_power_usage.py, sync_power_rates.py | |
| sync_supercharger_rates.py | sync_chargers_tesla_rates.py | |
| sync_invoices.py | sync_invoices.py | already fine |
| sync_dashcam.sh | sync_teslacam.sh | |
| analyse_drives.py (and tessie_drives_analyzer.py) | analyse_drives.py | |
| analyse_charging.py (and tessie_charging_analyzer.py) | analyse_charges.py | |
| analyse_battery_health.py | analyse_battery.py | |
| analyse_car_efficiency.py | analyse_efficiency.py | |
| analyse_fsd_105.py, analyse_fsd_osm_limits.py | analyse_fsd_plateaus.py, analyse_fsd_limits.py | |
| cost_per_100km_report.py | report_cost_per_100km.py | |
| report_all_charges_cost.py | report_charges_cost.py | |
| power_cost_calculator / demand_calculator / coverage_report / invoice_report / roi_report / solar_report | report_power_cost / _demand / _coverage / _invoice / _roi / _solar | |
| price_history_report.py | report_prices.py | |
| ev_session_cost.py | report_charge_session_cost.py | |
| drive_report.py | build_drive_pages.py | |
| drive_gps.py | build_drive_gps.py | |
| drive_actions.py | run_drive_actions.py | |
| build_fsd_map.py | build_fsd_map.py | already fine |
| teslacam_scan.py | scan_teslacam_telemetry.py | |
| teslacam_ledger.py | scan_teslacam_files.py | |
| teslacam_stills.py | make_teslacam_stills.py | |
| charger_find.py, find_tesla_chargers.py, find_plugshare_chargers.py | find_chargers.py (one command, providers in `providers_chargers/`) | |
| charger_plugshare_auditor.py | audit_chargers_plugshare.py | |
| places_manager.py (and tessie_places.py) | manage_places.py | |
| places_osm_editor.py | edit_places_osm.py | |
| dashcam_flip_review.py, sign_auto.py, sign_scan.py, tessie_vs_cam.py | review_dashcam_flips.py, scan_signs_auto.py, scan_signs.py, compare_tessie_cam.py | |
| dashcam_exportdash.sh | export_dashcam_overlay.sh | |
| Shared code | `lib/` | names unchanged |
| Tests | `tests/` | |
| fix_*, migrate_*, frac_fill, build_mac_app, home_charge_deep_dive | `maintenance/` with the same verbs | one-off, run by hand |

Every old name stays as a two-line stub that calls the new one (and says so) for a transition period.

## How the app calls the work (transparent to the user)

1. **Work lives in `lib/` as named tasks**, e.g. `sync_alerts`, `sync_drives`, `build_drive_pages`, `make_teslacam_stills`. A task has an id, a title in plain words, an area, its options (typed, with defaults from `config.json`), and a function that reports progress and checks a stop flag.
2. **A command-line script is a thin wrapper** over one task (parse options, call the task, print progress). The man-page help is generated from the task's description and options, so the help text and the app's options cannot drift apart.
3. **The app calls the same task in-process** through one runner and one set of endpoints: list tasks, start one, poll progress and output, stop it, read the last result. The existing charger sync, queue and review jobs move onto this runner.
4. **The user sees actions, not scripts.** "Sync alerts", "Rebuild drive pages" and "Take stills of new footage" appear where the data is (Alerts tab, Drives tab, TeslaCam tab) and in one Activity panel showing what is running, progress, result and any error text (selectable). Output from a run is kept as a plain log. A script name appears nowhere in the app.
5. **Long jobs keep working when the tab closes**, and the runner refuses to start two jobs that write the same data at once. Jobs that read a TeslaCam drive say so, because they keep the drive busy until it is released.

## Order of work

1. This plan (agreed).
2. Task registry and runner in the app; move charger sync, queue and review jobs onto it; one Activity panel.
3. Merge the eight duplicate pairs (after a `diff` of each), move shared code and tests, add `maintenance/`.
4. Rename with stubs, update the shell wrappers, docs and `config.json` references in one pass.
5. Convert each domain's sync and reports to tasks, one domain per commit, starting with alerts, drives and TeslaCam.
6. Man-page help for all 66 scripts without it, generated from the task definitions.

Each step is its own small commit, and nothing is deleted until its replacement is verified.
