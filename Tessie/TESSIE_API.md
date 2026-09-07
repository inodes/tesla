# Tessie Developer API — Notes for Future Sessions

> Quick-orientation doc so a future session (agent or human) doesn't have to
> re-research this from scratch. Official reference, always authoritative:
> **https://developer.tessie.com/reference/about**

## Why this exists

Right now the CSV exports this repo's tools consolidate (`drives_master.csv`,
`charges_master.csv`) are downloaded by hand from the Tessie web app. The
Tessie Developer API can return the same data programmatically, which would
let a sync tool replace the manual download step. This doc captures what was
learned evaluating that idea; it is not itself a client library or a
guarantee the shapes below are exhaustive — check the official reference for
anything this doc doesn't cover.

## Auth

- Bearer-style token passed as a query parameter: `?access_token=TOKEN`
  (not an `Authorization` header).
- Generate a long-lived personal access token at
  **https://dash.tessie.com/settings/api**. It's tied to the user's own
  Tessie account/vehicles — never share it or commit it.
- No paid Tessie tier is required just to poll historical `/drives` and
  `/charges` data.

**Config key**: store it as `"tessie_access_token"` in `Tessie/config.json`
(gitignored, same treatment as the existing `"vin"` key). This matches the
repo's existing flat snake_case config style (`vin`, `tessie_directory`,
`invoices_directory`) rather than nesting it under a new sub-object.

## Endpoints used so far

Base URL: `https://api.tessie.com`

- `GET /{vin}/drives` — trip/drive records.
- `GET /{vin}/charges` — charging session records.

Common query params:
- `access_token` (required, see above)
- `from`, `to` — Unix timestamps, filter by date range
- `limit` — cap the number of records returned
- `format=csv` — returns CSV directly instead of JSON, with columns that
  should line up closely with the manual Tessie export columns
  `tessie_drives_analyzer.py`/`tessie_charging_analyzer.py` already parse
  (worth diffing header-by-header before wiring up a real sync, since exact
  column names/order aren't confirmed identical).

Default (no `format`) response shape is JSON, typically
`{"results": [ {...}, {...} ]}`.

### Confirmed field names (live test, 2026-09-06)

`./Tools/test_tessie_api.py` was run against the user's real vehicle/token
(outside this session, on their own Mac, since `api.tessie.com` is blocked
from every environment this session can reach). A default (JSON, not
`format=csv`) `/drives` and `/charges` request each returned 5 real records.
Confirmed field names:

- **`/drives`**: `id`, `tag`, `started_at`, `ended_at`, `created_at`,
  `updated_at`, `import_id`, `starting_location`, `starting_saved_location`,
  `starting_latitude`, `starting_longitude`, `starting_odometer`,
  `starting_battery`, `ending_location`, `ending_saved_location`,
  `ending_latitude`, `ending_longitude`, `ending_odometer`,
  `ending_battery`, `odometer_distance`, `average_speed`, `max_speed`,
  `energy_used`, `rated_range_used`, `ideal_range_used`,
  `autopilot_distance`, `average_inside_temperature`,
  `average_outside_temperature`.
- **`/charges`**: `id`, `import_id`, `started_at`, `ended_at`, `created_at`,
  `updated_at`, `location`, `saved_location`, `latitude`, `longitude`,
  `odometer`, `starting_battery`, `ending_battery`, `energy_added`,
  `energy_used`, `miles_added`, `miles_added_ideal`, `max_range`,
  `max_ideal_range`, `capacity`, `cost`, `is_supercharger`,
  `is_fast_charger`, `since_last_charge`.

**Important**: these are `snake_case` API field names, not the manual-export
CSV headers (`"Started At"`, `"Starting Location"`, `"Distance (km)"`,
`"Supercharging (kWh)"`, etc.) that `--consolidate` currently parses in
`tessie_drives_analyzer.py`/`tessie_charging_analyzer.py`. A sync tool built
on this API is **not** a drop-in replacement for the CSV pipeline as-is — it
would need its own field-mapping/translation layer (e.g.
`odometer_distance` → `Distance (km)`, mindful of unit differences; miles
vs km fields exist on the charges side too) rather than reusing the CSV
parsing logic unchanged. `saved_location`/`starting_saved_location`/
`ending_saved_location` look promising for matching against this repo's own
named-places registry (`places.json`) without needing a fresh
reverse-geocode, but that hasn't been tested yet.

`?access_token=` auth, VIN-scoped, JSON response shape, and both endpoints
returning real data are now all confirmed working end to end — this is no
longer just theoretical.

## Full documented query parameters (from the official reference)

`GET /{vin}/drives`: `distance_format` (`mi`|`km`, default `mi`),
`temperature_format` (`c`|`f`, default `c`), `from`, `to` (Unix seconds),
`timezone` (IANA name, default `UTC`), `origin_latitude`/`origin_longitude`/
`origin_radius`/`exclude_origin`, `destination_latitude`/
`destination_longitude`/`destination_radius`/`exclude_destination`, `tag`/
`exclude_tag`, `driver_profile`/`exclude_driver_profile`, `format`
(`json`|`csv`, default `json`), `minimum_distance` (miles), `limit`.

`GET /{vin}/charges`: `distance_format` (`mi`|`km`, default `mi`), `format`
(`json`|`csv`, default `json`), `superchargers_only`, `origin_latitude`/
`origin_longitude`/`origin_radius`/`exclude_origin`, `timezone` (default
`UTC`), `from`, `to`, `minimum_energy_added` (kWh), `limit`.

**This resolves the earlier units/timezone open question**: the API
defaults to miles and UTC unless told otherwise. Passing
`distance_format=km&temperature_format=c&timezone=Australia/Sydney` gets
values in the same units/timezone this repo's manual CSV exports already
use, so `test_tessie_api.py` now sends those three by default
(`--distance-format`/`--temperature-format`/`--timezone` to override) rather
than leaving it to guesswork or a later conversion step.

The `origin_*`/`destination_*` radius filters are also worth remembering
for later — they could let a sync tool pull "drives that started/ended
within N meters of a saved place" directly from the API instead of
re-implementing that filter locally.

## Open questions / not yet confirmed

- **Pagination**: no cursor/next-page mechanism is listed in the parameter
  reference for either endpoint — `from`/`to`/`limit` slicing remains the
  safe assumption for a long history (5 records is not a large-history
  test).
- **Rate limits**: nothing aggressive has been hit yet, but a 5-record test
  doesn't exercise this. Treat like the PlugShare lesson learned elsewhere in
  this repo (BUG-008) — don't burst-request without backoff.
- ~~`format=csv` returned HTTP 500`~~ — **fixed and confirmed**: was the
  script's hardcoded `Accept: application/json` header clashing with a
  `format=csv` request. Sending `Accept: text/csv` for CSV requests fixed it;
  re-run by the user succeeded, samples saved to
  `Tessie/api_test_samples/sample_drives.csv` /
  `Tessie/api_test_samples/sample_charges.csv`.

## Gap analysis result (2026-09-06) — no gaps

Compared the live `format=csv&distance_format=km&temperature_format=c&
timezone=Australia/Sydney` samples header-by-header, programmatically,
against the real `Tessie/drives/drives_master.csv` and
`Tessie/charges/charges_master.csv` already on disk:

- **Drives**: all 26 columns, same names, same order, same units — **byte-
  for-byte identical header**. Tessie's own CSV formatter already computes
  `Duration (Minutes)` and `Average Energy Used (Wh/km)` server-side, so no
  local derivation is needed either.
- **Charges**: the manual export's 17 columns are all present, in the same
  order, with identical names and units (`Cost Per kWh` included, also
  computed server-side). The API adds 4 extra columns not in the manual
  export — `Distance Since Last Charge (km)`, `Max Range (km)`,
  `Max Ideal Range (km)`, `Capacity (kWh)` — which is a bonus, not a gap.

**Conclusion**: with `distance_format=km`, `temperature_format=c`,
`timezone=Australia/Sydney`, and `format=csv`, the API output for drives is
already schema-identical to a manual export and can be consumed by
`--consolidate` as-is; charges only needs the 4 bonus trailing columns
dropped (or kept — `tessie_charging_analyzer.py`'s CSV-schema detection is
based on indicator columns being present, so extra columns shouldn't break
it, but this hasn't been tried yet). No real gaps found — the API is not
missing anything the manual export has.

## Row-level verification (2026-09-06, 14-day / 50-drive sample)

Schema match isn't the same as data match, so this went one step further:
pulled `--since-days 14 --limit 50 --save-csv` (50 drives, 31 charges) and
matched every row against the real `drives_master.csv`/`charges_master.csv`
on the user's iCloud drive by `Started At (AEST)`, comparing every shared
column value-for-value.

- **Drives**: all 50 API rows matched a real row by timestamp (0 unmatched).
  Every single field was identical - lat/long, odometer, battery %, energy,
  speeds, temperatures, everything - except `Starting/Ending Saved
  Location`, which differed on 45 of 50 rows.
- **Charges**: 27 of 31 API rows matched a real row by timestamp; same
  result - every field identical except `Saved Location`. The other 4 had no
  matching timestamp at all, but that's not a data problem: the real
  `charges_master.csv`'s last recorded charge is `2026-09-05 09:37`, and all
  4 unmatched rows are from *after* that (`2026-09-05 14:34` onward) - they
  simply haven't been manually downloaded yet. This is itself a live
  demonstration of the value of API sync: those 4 real charging sessions
  already exist and are sitting unrecorded in the master file purely because
  nobody's re-exported since.

**Why Saved Location differs (expected, not a bug)**: the master CSVs were
last consolidated from a manual export taken at some point in the past;
`Saved Location` values in that snapshot are already-stale Tessie-app
labels frozen at export time (e.g. blank, or a raw street address like
`"1108 Victoria Rd"`). The live API reflects Tessie's *current* saved-location
state (e.g. `"Home"`, `"Goulding Hill Preschool"`, `"REIN Garage"`) - names
the user has since added/renamed in the Tessie app itself. This is exactly
the BUG-016 concern in `AGENTS.md`: Tessie's saved-location text is mutable
and can drift out of sync with any point-in-time snapshot, which is why
`resolve_place()` no longer trusts it over GPS-registry matching. It doesn't
affect this verification's conclusion - every other field matched exactly,
row for row.

**Overall conclusion, now proven at the row level, not just the header
level**: the API (with `distance_format=km&temperature_format=c&
timezone=Australia/Sydney&format=csv`) reproduces the manually-exported data
exactly, field for field, and picks up sessions the manual export hasn't
caught up to yet. Nothing is missing; nothing numeric differs.

## Test script

`Tools/test_tessie_api.py` is a one-shot connectivity/shape check — it makes
no changes to any registry or master CSV. It reads the token from
`--token`, then `TESSIE_ACCESS_TOKEN`, then `Tessie/config.json`'s
`tessie_access_token` key, and the VIN from `--vin` or the existing `"vin"`
config key. Run it with `--help` for all flags (`--limit`, `--since-days`,
`--save-csv`, `--show-values`).

**Known limitation**: `api.tessie.com` is blocked by this environment's own
sandbox network allowlist (same class of restriction already documented for
PlugShare/Overpass in `CLAUDE.md` §1) — from both the cloud container and
the device-bridged sandbox VM used by this session. This script has been
written and `py_compile`-checked but **could not be executed live from
here**. It needs to be run from the user's own Mac with a real token to
confirm anything about the live API's actual behavior.


## Sync tool: `Tools/tessie_api_sync.py` (2026-09-06)

Replaces the manual "download CSV, drop it in Downloads" workflow. Fetches
via the API and writes a CSV into the landing directory in the exact shape
a manual export already has - everything after that (merging into
`drives_master.csv`/`charges_master.csv`, invoice reconciliation, archiving)
is left entirely to the existing `--consolidate` flows in
`tessie_drives_analyzer.py`/`tessie_charging_analyzer.py`, unchanged. This
tool's only job is fetching, deliberately kept separate from those two
analyzer scripts rather than bolted onto each one - a shared
`Tools/tessie_api_common.py` module (config/token loading, the HTTP call)
is used by both this and `test_tessie_api.py`, so there's one implementation
of the API plumbing, not two drifting copies.

```
export TESSIE_ACCESS_TOKEN=your_token_here
./Tools/tessie_api_sync.py                      # fetch + prompt to consolidate, both types
./Tools/tessie_api_sync.py --yes                # fetch + consolidate without prompting (cron)
./Tools/tessie_api_sync.py --no-consolidate      # fetch only, leave CSV(s) for you to merge later
./Tools/tessie_api_sync.py --drives             # just drives
./Tools/tessie_api_sync.py --since-days 180     # first-run backfill window (default: 90)
./Tools/tessie_api_sync.py --dry-run            # fetch and report, write/consolidate nothing
./Tools/tessie_api_sync.py --reset-cache --charges  # forget prior sync point, re-backfill
```

**Consolidation is built in, not a separate manual step.** After writing a
CSV, the tool shells out to the existing analyzer script's own
`--consolidate` flag (`tessie_drives_analyzer.py --consolidate` /
`tessie_charging_analyzer.py --consolidate`) as a subprocess - the exact
command you'd type by hand, never imported or reimplemented, so those two
scripts don't need to change at all. Interactively it asks for confirmation
first; non-interactively (e.g. a cron job) it refuses to consolidate unless
`--yes` is also passed, so unattended automation has to opt in explicitly
rather than silently merging into your master files. `--no-consolidate`
fetches only, same as the tool's original behavior, if you'd rather keep
that as a separate manual step for a while.

**JSON + CSV, and why**: a local JSON cache (`Tessie/api_cache/<type>.json`,
gitignored - real personal telemetry) keyed by the API's own record `id` is
what makes re-running this idempotent and, from the second run on,
incremental (only the window since the last successful sync is re-fetched,
with a 1-day overlap for late server-side edits). The CSV export has no
`id`/`updated_at` columns at all, so none of that bookkeeping is possible
from CSV alone. The CSV actually written to the landing directory, though,
comes straight from Tessie's own `format=csv` output rather than being
hand-derived from the JSON fields - testing found at least one derived
column (`Cost Per kWh`) is computed server-side from full-precision values
before rounding, so re-deriving it from the already-rounded JSON fields can
disagree slightly. Trusting Tessie's own CSV formatting avoids that class of
bug entirely.

**No pagination cursor is documented for either endpoint** (confirmed against
the official parameter reference), so a JSON fetch that might be truncated
by `limit` is split into two half-windows and re-fetched recursively until
every leaf request comes back under its own limit, then merged and deduped
by `id`. Hardened with a mocked test: if a window still can't be split
further (under 1 hour) while still sitting at the limit ceiling - which
would mean `chunk_limit`-or-more drives/charges inside one hour, not
realistic for a personal vehicle - it raises loudly instead of silently
returning a truncated result.

Also verifies the CSV and JSON fetches agree on record count for the same
window and warns (but still saves) if they don't, in case a future account
or endpoint behaves differently than the one this was built and tested
against.

**Tested with mocked API responses only** (network to `api.tessie.com` is
blocked from every environment available to this session, same as always) -
covers: fresh sync, idempotent no-op re-run, detecting a changed record,
`--dry-run` never touching disk, `--reset-cache`, chunking recovery at
realistic data density, the pathological-density safety raise, the
CSV/JSON row-count mismatch warning, and clean HTTP-error surfacing.
Since live-verified end to end from the user's own Mac: a `--dry-run`
correctly fetched 991 drives + 193 charges with real chunking exercised,
and a real (non-dry-run) run wrote both CSVs successfully.

### BUG-019: sync cache advanced on fetch success alone, not on safe merge (2026-09-07)

**Found by the user**, not by testing: after a real sync left two CSVs
sitting in `~/Downloads` waiting at the `--consolidate` prompt, he asked
"this would not work if the original csv's were deleted?" - a sharp catch
of a real architectural flaw.

**The bug**: `sync_one_type()` used to call `save_cache()` unconditionally
whenever `not dry_run`, immediately after a successful fetch and CSV write
- before `--consolidate` had ever run. That save both records every fetched
record's raw JSON under its `id` *and* advances `_meta.last_synced_to`
(the "already synced" watermark). If the just-written CSV was then deleted
or otherwise lost before being consolidated into the master file, a later
sync would see `new_or_changed == 0` for that window (the cache already
"remembered" those records) and would never re-fetch or re-surface that
data again - it was permanently excluded, with no way to recover it short
of `--reset-cache` and a full backfill.

**Confirmed via reproduction** (not from an actual loss - the real CSVs
in `~/Downloads` were never deleted): fetched 5 fake records, got a
`csv_path` back, deleted that file, then re-ran `sync_one_type()` with the
identical fake data - the second run reported `new_or_changed: 0,
csv_path: None`, proving the data was gone from the tool's perspective.

**The fix**: decouple "fetched from the API" from "durably merged into the
master file" - two different guarantees that were wrongly conflated.
`sync_one_type()` no longer saves the cache itself when there's a pending
CSV; it commits immediately only when `new_or_changed == 0` (nothing
written, nothing at risk), and otherwise returns the in-memory cache via a
`pending_commit` key. A new `commit_cache(spec, cache, to_ts)` function
does the actual save. `main()` now only calls `commit_cache()` for a type
once that type's `--consolidate` subprocess has actually returned success;
if consolidation is declined, skipped (`--no-consolidate`, non-interactive
without `--yes`), or fails, the cache is left uncommitted on purpose - the
next sync will simply re-fetch and re-surface the same window again,
because the watermark was never advanced. Nothing is ever silently lost;
at worst a pending window gets re-fetched more than once, which the
existing content-key dedup in `--consolidate` already handles cleanly for
byte-identical re-fetches.

Verified with a 15-check mocked reproduction (the exact original bug
scenario now proven fixed: run 1 fetches 5 records and does NOT commit;
the CSV is deleted; run 2 still sees all 5 as new and writes a fresh,
recoverable CSV; simulating a successful `--consolidate` via
`commit_cache()` correctly advances the watermark; a third run then
correctly sees nothing new) plus a full regression re-run of every
previously-passing scenario (chunking at realistic and pathological
density, all `should_consolidate`/`run_consolidate` branches) and a live
`--help`/`py_compile` check on the real script.

No real data was ever actually lost - the 4 real CSVs from the user's own
syncs were still sitting untouched in `~/Downloads` when this was found and
fixed.
### REQ-025: CSV filenames named after the data, not the fetch clock (2026-09-07)

User suggestion: name the written CSV after the *latest entry the fetch
actually found* rather than the wall-clock moment the sync command was run
- a run at 5pm covering a drive that ended at 8am should be named after
8am, not 5pm - and guard against two files landing on the same name.

**Implemented**: `latest_entry_timestamp(records)` takes the max of each
fetched record's `ended_at` (falling back to `started_at` for the rare
record with no end yet) across the whole window's fetch, and that becomes
the file's timestamp component instead of `datetime.now()`.
`unique_csv_path(landing_dir, prefix, stamp)` then checks for an existing
file at that name and, only if one exists, appends a zero-padded numeric
suffix (`_01`, `_02`, ...) until it finds a free one - the first file for a
given stamp still gets the plain, unsuffixed name.

**Is clobbering actually possible, given the BUG-019 cache fix? Yes** -
the durability fix stops the *watermark* from silently swallowing data, but
it doesn't stop two different, still-unconsolidated fetches from computing
the identical "latest entry" stamp:
- A field-level edit to an already-seen record (exactly what was found live
  - see the `Distance Since Last Charge` example in the gap-analysis notes
  above) doesn't move the max timestamp, so a re-fetch triggered by that
  edit alone can land on the same stamp as a prior pending file.
- Re-running the sync before ever answering the `--consolidate` prompt
  re-fetches the same pending window every time (by design, per BUG-019 -
  the watermark isn't advanced until consolidation actually succeeds), so
  it will keep computing the same "latest entry" stamp on every re-run
  until you consolidate.

Verified with 13 mocked checks: the filename reflects the data's latest
`ended_at` rather than an unrelated "now" passed as the fetch clock;
`unique_csv_path()` hands out the plain name first and `_01`/`_02` on
repeated requests; the exact `BUG-019` delete-before-consolidate scenario
still recovers all records and reuses the now-free base name; and, without
deleting anything, a second still-pending run against the same window gets
the `_01` suffix instead of clobbering the first file - both files verified
to still exist on disk afterward. Full existing test suite re-run
alongside it with no regressions.

### BUG-020: `--consolidate` wasn't a real flag on `tessie_api_sync.py` (2026-09-07)

User ran `./Tools/tessie_api_sync.py --consolidate` - a reasonable thing to
type, since that's the flag name used everywhere in this doc for the
underlying analyzer scripts - and got `error: unrecognized arguments:
--consolidate`. The sync tool only ever exposed `--yes` (auto-consolidate
after a fresh fetch) and `--no-consolidate` (skip it); `--consolidate`
belongs to `tessie_drives_analyzer.py`/`tessie_charging_analyzer.py`
themselves.

**Fixed** by adding a real `--consolidate` flag to `tessie_api_sync.py`
too: it skips fetching entirely (no token/VIN needed for this mode at all)
and runs `run_consolidate()` directly for each selected type - the exact
same subprocess call as typing each analyzer's own `--consolidate` by
hand, just reachable from one command regardless of which script you think
of first. Rejected with exit code 2 if combined with `--no-consolidate`
(contradictory) or `--dry-run` (this mode has no fetch to be "dry" about).

```
./Tools/tessie_api_sync.py --consolidate            # merge whatever's pending, both types
./Tools/tessie_api_sync.py --consolidate --drives   # just drives
```

Verified with 7 mocked/subprocess checks (both conflict cases exit 2 with
a clear message; `--consolidate --drives` gets past the token/VIN checks
with neither error printed).

**A first attempt to also verify the real outcome was wrong.** Running
`--consolidate` for real from this session's own sandboxed shell reported
"Consolidated 1055 total drives (+0 new)" / "appended 0 new charges (Total:
197)" and was written up here as confirmation the 4 pending CSVs were
redundant. That was a false negative caused by a path-resolution trap, not
a real result: in that shell, `~/Downloads` (`Tessie/config.json`'s
`landing_directory`) expands to that shell's own home directory, which
doesn't have a `Downloads` folder at all - the real `/Users/glenn/Downloads`
is only reachable there via a `~/mnt/Downloads` mount alias that a
subprocess launched from `tessie_api_sync.py` has no way to know about. So
the analyzer scripts found zero landing files to ingest - "+0 new" meant
"0 files processed", not "0 new among files genuinely processed" - and
nothing was archived because nothing was ever read.

**The user then ran it for real on his own Mac** and got the true result:
one `--consolidate` command (both types) printed "Ingested & Archived" for
all 4 pending CSVs, "Consolidated 1058 total drives (+3 new)", and
"Successfully appended 4 new charges (Total: 201)" - genuinely new data,
correctly merged into both master files and archived. That run is
authoritative; the "safe to delete, fully redundant" claim from the earlier
sandboxed check was wrong.

**Takeaway for future sessions**: don't treat a `~`-relative path resolved
inside this session's own device-bridge shell as equivalent to the same
path on the user's machine - real verification of anything that reads a
`~`-relative config value needs the user to run it themselves.

### BUG-021: `resolve_location()` matched by "first entry reached", not "best entry" (2026-09-07)

User's `--third-party` output showed a real, GPS-confirmed charge at
Bunnings Gladesville (Sydney) displayed as **"Emirates One & Only Wolgan
Valley"** (a resort ~150km away) and a separate no-GPS Bunnings charge
displayed as **"Bunnings Bellambi"** (Wollongong).

**Root cause**: `resolve_location()` checked each registry entry in turn -
saved-location exact-or-keyword match, then GPS radius, then address
keyword match - and returned the instant ANY tier hit for THAT entry. The
keyword lists are scraped verbatim from PlugShare place names/addresses
and include generic words with zero power to disambiguate: `"Bunnings"`
appears in 6 different stations' keyword lists, `"Australia"` in nearly
every entry in the file. So a random early entry sharing nothing but a
generic word could win outright over the CORRECT station's own exact GPS
match or exact saved-location match sitting later in the same registry.
Confirmed directly against the real data: a `Bunnings Gladesville` entry
sits ~28m from the real GPS coordinates (well inside its own 150m radius)
and matches the saved-location string exactly - it should always have won.

**Fixed**: `resolve_location()` now ranks matches by priority across every
registry at once, not per-entry:
1. GPS radius, nearest wins globally - with a purpose-built charging
   registry (Superchargers/personal/PlugShare/destination) preferred over
   a same-location entry that only exists in the generic `places.json`
   (added for drive-matching, not charging, so it carries no network name)
   even at a near-tied distance.
2. Exact saved-location name match, also global.
3. Keyword substring match, last resort only, restricted to keywords that
   are both ≥5 characters AND unique across the *entire* combined registry
   (computed once via a frequency count, memoized on the instance) - so
   `"Bunnings"`/`"Australia"` can never win, while `"Bellambi"`/`"Wolgan"`
   still can for a station that actually needs them.

Verified with 9 checks against the real registry files and the real
charges' own coordinates pulled straight from `charges_master.csv`: both
misresolved sessions now correctly resolve to `Bunnings Gladesville` /
`Exploren`; all 5 already-correct Supercharger sessions (Macquarie, West
Gosford ×2, Miranda) still resolve correctly; the `places.json` duplicate
no longer steals the network label from the more specific PlugShare entry.

### TODO-009: timezone handling needs to be UTC-anchored, not one fixed zone (2026-09-07)

User's concern: charging location matters for local time, and using ONE
fixed timezone for everything breaks down across a real border crossing -
e.g. a charge in NSW, then one in SA 30 minutes later, are on different
real UTC offsets (SA is 30/60 min behind NSW depending on daylight saving).
"In computing, UTC is king."

**What's actually true today, verified rather than assumed**:
- The API's `from`/`to` request params are Unix-epoch seconds - always
  UTC by construction, already correct in `tessie_api_sync.py`.
- The JSON response's `started_at`/`ended_at` fields are (per the official
  parameter reference and the `started_at`/`created_at`/`updated_at`
  naming convention) also true Unix-epoch UTC, unaffected by the
  `timezone` request parameter - that parameter only controls how Tessie
  FORMATS its own `format=csv` text output. So `tessie_api_sync.py`'s JSON
  cache (`Tessie/api_cache/<type>.json`) is already UTC-correct and needs
  no change.
- The bug is downstream, in every CSV - manually-exported AND API-fetched
  alike: every date column (`"Started At (AEST)"` etc.) is pre-converted
  by Tessie to a SINGLE FIXED `Australia/Sydney` zone (`DEFAULT_UNIT_PARAMS`
  in `tessie_api_common.py`) before this repo ever sees it, and both
  analyzer scripts parse/sort/display those already-localized strings with
  no timezone awareness at all. A charge that genuinely happens in SA gets
  a WRONG displayed local time (offset as if it were in Sydney), and the
  static `"(AEST)"` header is itself inaccurate for roughly half the year
  whenever daylight saving (AEDT) is actually in effect, even for a
  genuinely-Sydney event.
- A subtler risk exists even with NO cross-timezone travel at all: naive
  string/datetime comparison across a DST fall-back transition (clocks
  repeat an hour, twice a year, ~1hr window) could misorder two
  Sydney-only events.

**Not yet fixed** - this is an architecture-level change (CSV schema, plus
both analyzer scripts' parsing/sorting/display logic), not a quick patch,
and needs a scoping discussion first: recommended direction is to anchor
everything on true UTC (already true for the JSON cache) and only convert
to a REAL per-event local time using that event's own GPS-derived
timezone (not one global default), with an explicit numeric offset shown
(e.g. `+11`) rather than an ambiguous acronym like `AEST`/`AEDT`.

### BUG-022: third-party location/network trusted Tessie's own guess over the paid invoice (2026-09-07)

User's `--third-party` output showed all 3 sessions as **"Bunnings
Gladesville"**, including invoice `0000533887` - but that invoice's own
filename and printed content (`Location: Rydalmere, Bunnings Rydalmere`)
say **"Bunnings Rydalmere"**, a different store. User's own suggested
design: since an invoice (Tesla's or a third party's like Exploren) always
prints a literal, correct station name, that should be recorded against
the invoice number itself - "maybe an invoice to location mapping could be
written out beside the invoice files?"

**Root cause**: a matched charging session's displayed `place_name`/
`network` was computed ONCE at charge-load time from Tessie's own
`Saved Location` CSV field via `resolve_location()` - the same
manually-entered, sometimes-stale source already shown unreliable in
BUG-016/BUG-021 - and never revisited even after the reconciliation step
had already identified exactly which invoice paid for that session. The
existing invoice-to-charge pairing logic (`match_location()`, token
overlap between the invoice's own location text and the charge's raw
address/saved-location text) was already correct and untouched; only the
value actually *displayed* was wrong.

**Fixed**, implementing the user's own suggested design:
1. New `resolve_invoice_location()`: once an invoice is matched to a
   session, re-resolves place/network/emoji from the INVOICE's own printed
   location text (not Tessie's saved_location), via the same
   `resolve_location()` used elsewhere.
2. New `invoice_locations.json`, written beside the invoice files
   (`self.invoice_dirs[0]`) after every `reconcile()` run: a persistent,
   hand-correctable invoice-number -> {place_name, network, emoji,
   location_raw, last_verified} map. An entry with `"manual_override":
   true` is never recomputed - a human fix stays fixed.
3. The invoice's own printed network (parsed from its supplier line, e.g.
   `"Exploren"`) is now trusted over whatever `resolve_location()` guesses
   for the network specifically - its GPS/keyword match can land on a
   plain `places.json` entry (added for drive-matching, no operator name
   of its own), which used to fall back to a generic label like `"DC
   Fast"` instead of the invoice's real network.

**Two follow-on bugs found while verifying this fix against the real
invoice/registry data** (same transparency as BUG-020/BUG-021 - both
caught before being reported as fixed, not after):
- Attempt 1 introduced a NEW wrong match: sessions genuinely at "Bunnings
  Gladesville" started resolving to **"The Ville Resort Casino"** (a
  Townsville casino) - the keyword `"Ville"` (from that casino's own
  keyword list, globally unique by raw count and ≥5 characters) matched as
  a plain substring inside the unrelated word "gladesVILLE". **Fixed** by
  switching the keyword tier to word-boundary regex matching
  (`\bKEYWORD\b`) instead of a plain substring check.
- That word-boundary fix then UNDER-matched the same two sessions,
  falling through to the generic `"Gladesville"`/`"3rd-Party Fast"`
  fallback instead of the correct `"Bunnings Gladesville"`/`"Exploren"`.
  Cause: `plugshare_chargers.json` has TWO dict entries for the exact same
  real store - `"Bunnings Gladesville"` and `"Bunnings Gladesville
  (PlugShare #1517863)"` (a re-scrape duplicate) - so the keyword phrase
  `"Bunnings Gladesville"` had a raw entry-count of 2 and failed the
  keyword tier's "count == 1" uniqueness check, even though both entries
  agree on the same place. **Fixed** by counting keyword uniqueness by
  DISTINCT NORMALIZED DISPLAY NAME (stripping a trailing `"(...)"` suffix
  before comparing) instead of raw entry count, so duplicate registry rows
  for one real place collapse to one identity while genuinely shared words
  (`"Bunnings"` alone spans 6 stores, `"Gladesville"` alone spans 3) stay
  excluded.

Verified against the real invoice PDFs and registries (`--invoices-dir`
pointed at the real, mounted `charging_invoices` folder - see BUG-020's
`~`-path lesson for why an explicit path is needed here rather than the
config default when testing from this session): all 3 third-party
sessions now resolve correctly - `0000533887` -> Bunnings Rydalmere /
Exploren, `0000545157` and `0000555286` -> Bunnings Gladesville /
Exploren - and all 5 Supercharger sessions still resolve correctly with no
regression. `invoice_locations.json` was written to the real invoices
folder with the correct entries for all 8 loaded invoices.
