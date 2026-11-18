# Tessie Developer API reference

Notes on the parts of the Tessie API this repo uses. The official reference is authoritative: <https://developer.tessie.com/reference/about>. Check it for anything not covered here.

## Auth

- The token goes in the query string: `?access_token=TOKEN` (not an `Authorization` header).
- Create a long-lived personal access token at <https://dash.tessie.com/settings/api>. It belongs to your own Tessie account and vehicles; never share it or commit it.
- Polling historical `/drives`, `/charges` and `/states` needs no paid tier.
- Store it as `"tessie_access_token"` in `config.json` (git-ignored) next to `"vin"`. Commands also read `TESSIE_ACCESS_TOKEN` and `--token`.

## Endpoints

Base URL `https://api.tessie.com`. All endpoints are scoped to a VIN: `GET /{vin}/...`.

| Endpoint | Returns |
|---|---|
| `/drives` | Trip records |
| `/charges` | Charging session records |
| `/states` | Per-sample vehicle history for a time window (the data behind a detailed telemetry export) |

Responses are JSON, `{"results": [...]}`, unless `format=csv` is given. Field names are `snake_case`, not the manual CSV export headers (`Started At`, `Distance (km)`, and so on), so API data goes through a field-mapping layer. Mind units: the API defaults to miles and UTC.

## Query parameters

`/drives`: `distance_format` (`mi`|`km`, default `mi`), `temperature_format` (`c`|`f`, default `c`), `from`, `to` (Unix seconds), `timezone` (IANA name, default `UTC`), `origin_latitude`/`origin_longitude`/`origin_radius`/`exclude_origin`, `destination_latitude`/`destination_longitude`/`destination_radius`/`exclude_destination`, `tag`/`exclude_tag`, `driver_profile`/`exclude_driver_profile`, `format` (`json`|`csv`), `minimum_distance` (miles), `limit`.

`/charges`: `distance_format`, `format`, `superchargers_only`, `origin_latitude`/`origin_longitude`/`origin_radius`/`exclude_origin`, `timezone`, `from`, `to`, `minimum_energy_added` (kWh), `limit`.

`/states`:

| Param | Type | Notes |
|---|---|---|
| `from` / `to` | number | Unix seconds; the window to fetch |
| `interval` | number | Seconds between points; `1` asks for the finest grain |
| `condense` | boolean | Default `true` (arrays per field); `false` gives one object per sample |
| `timezone` | string | IANA name, default UTC |
| `distance_format` / `temperature_format` | string | As for `/drives` |
| `format` | string | `json` (default) or `csv` |

To match this repo's CSV exports, send `distance_format=km`, `temperature_format=c` and your own IANA `timezone`; the commands do this from `config.json`. For CSV output, send `Accept: text/csv`; a JSON `Accept` header with `format=csv` returns HTTP 500.

## Fields

- **`/drives`**: `id`, `tag`, `started_at`, `ended_at`, `created_at`, `updated_at`, `import_id`, `starting_location`, `starting_saved_location`, `starting_latitude`, `starting_longitude`, `starting_odometer`, `starting_battery`, `ending_location`, `ending_saved_location`, `ending_latitude`, `ending_longitude`, `ending_odometer`, `ending_battery`, `odometer_distance`, `average_speed`, `max_speed`, `energy_used`, `rated_range_used`, `ideal_range_used`, `autopilot_distance`, `average_inside_temperature`, `average_outside_temperature`.
- **`/charges`**: `id`, `import_id`, `started_at`, `ended_at`, `created_at`, `updated_at`, `location`, `saved_location`, `latitude`, `longitude`, `odometer`, `starting_battery`, `ending_battery`, `energy_added`, `energy_used`, `miles_added`, `miles_added_ideal`, `max_range`, `max_ideal_range`, `capacity`, `cost`, `is_supercharger`, `is_fast_charger`, `since_last_charge`.
- **`/states`** (all nullable): `id`, `timestamp`; `state`, `charging_state`, `shift_state`, `locked`, `sentry_mode`; `battery_level`, `usable_battery_level`, `battery_range`, `ideal_battery_range`, `energy_remaining`; `latitude`, `longitude`, `elevation`; `speed`, `odometer`; `charge_rate`, `charger_actual_current`, `charger_power`, `charger_phases`, `charger_voltage`, `charge_energy_added`, `charge_miles_added_rated`, `charge_miles_added_ideal`; `inside_temp`, `outside_temp`, `is_climate_on`, `battery_heater_on`, `module_temp_min`, `module_temp_max`; `power`, `pack_current`, `pack_voltage`, `lifetime_energy_used`.

Behaviour to rely on:

- `/states` has no fixed sampling interval. Even with `interval=1` Tessie applies its own cadence (about one sample every 40 s on a long home charge), so do not assume one point per second.
- `charger_power`, `charger_voltage` and `charger_phases` are populated for home AC charging as well as DC.
- `saved_location` fields can match this repo's named places without a fresh reverse geocode, but Tessie's saved-location text is mutable. Treat it as a hint and prefer the place registry or the paid invoice.
- No pagination cursor is documented for `/drives` or `/charges`; slice by `from`/`to`/`limit`.

## Rate limits

Do not burst-request; use backoff, as with other third-party APIs here. `api.tessie.com` is blocked from the cloud VM allowlist (see `AGENTS.md`); run API commands in your own terminal.

## Sync command

`./Tools/tesla sync tessie -h` documents every flag. How it works:

- It fetches through the API and writes a CSV in the same shape as a manual export into the landing directory. Merging into the master files, invoice reconciliation and archiving stay in the analyse commands' `--consolidate` flows.
- A local JSON cache (git-ignored: it holds personal telemetry), keyed by the API record `id`, makes re-runs idempotent and incremental: only the window since the last successful sync is fetched, with a one-day overlap for late server-side edits. The cache advances only after a safe merge, not on fetch success alone.
- The CSV written comes from Tessie's own `format=csv` output, not from the JSON, because server-side columns such as `Cost Per kWh` are computed from full-precision values and can differ from ones re-derived from rounded JSON.
- A fetch that might be truncated by `limit` is split in half and re-fetched recursively, then merged and de-duplicated by `id`. If a window under one hour still hits the limit it raises an error instead of returning a truncated result.
- The CSV and JSON record counts are compared for each window; a mismatch warns but still saves.
- Non-interactively it consolidates only with `--yes`; `--no-consolidate` fetches only; `--dry-run` writes nothing; `--reset-cache` forgets the last sync point.
- Files are named after the data they hold (the first and last record), not the fetch time, so two fetches can never overwrite each other.
- Timestamps are anchored to UTC and converted to the configured IANA timezone for display, not stored in one fixed zone.
- A matched third-party location or network is checked against the paid invoice before Tessie's own guess is trusted.

`Tools/lib/tessie_api_common.py` holds the shared token, config and HTTP code. `Tools/commands/` also has a one-shot connectivity and shape check (`./Tools/tesla` lists it); it changes nothing on disk.
