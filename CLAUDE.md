# CLAUDE.md — Claude (Cowork) Session Notes

> This file is Claude's own operating notes for working in this repo, kept alongside
> `AGENTS.md`. **`AGENTS.md` remains the canonical source of truth** for repo layout,
> table-formatting standards, the request/bug/TODO tracker, and the git/PII rules —
> read that first, every session. This file exists only to capture things specific to
> working here *as Claude in Cowork*, so they aren't rediscovered from scratch each time.

---

## 1. How this session reaches the repo

- This repo lives on the user's Mac at `~/repos/tesla`. Claude (Cowork) reaches it
  through a remote-device bridge, not a local clone — the bridge mounts it at
  `~/mnt/tesla` inside a sandboxed Linux VM that runs the `device_bash` tool. That VM
  is **not** the user's real shell: it has its own restricted network egress.
- **Known limitation**: this session's `device_bash` egress is blocked by a proxy
  allowlist for `api.plugshare.com` (confirmed via direct `curl` — returns
  `403 Forbidden` / `X-Proxy-Error: blocked-by-allowlist`). This means:
  - Running `find_plugshare_chargers.py --plugshare <id>` (or anything else that hits
    the live PlugShare API) from this session will silently report "not found" or
    empty data **even when the ID is valid**, because `PlugShareClient._get()`
    swallows non-404 HTTP errors (including this 403) and returns `None` — visually
    identical to a real 404.
  - **Do not trust a "not found" / empty result from this session as evidence the
    PlugShare ID or endpoint is broken.** Re-verify with the user running the same
    command in their own terminal (real network access) before concluding anything
    about the PlugShare API itself.
- A second cloud-container sandbox (used for scratch work, unrelated to
  `device_bash`) has separately been confirmed to also lack usable access to
  PlugShare's site (JS-rendered SPA; `WebFetch` only returns unreliable
  og:meta tags). Treat both paths as unable to independently verify live PlugShare
  data — the user's own browser/terminal is the only reliable source for that.

## 2. Working conventions specific to this session

- File edits on the repo happen via `device_bash` python heredocs doing exact
  substring match-and-replace with an `assert count == 1` guard before writing —
  never blind re-typing of file content from a prior (possibly truncated) tool read.
- After every edit: `python3 -m py_compile <file>` to catch syntax errors immediately
  (per `AGENTS.md` §7's own checklist).
- Per `AGENTS.md` §1.2, **never commit without first describing the change and the
  exact proposed commit message(s) to the user and getting a go-ahead** — this
  applies even though Claude, not Gemini Antigravity, is now the agent doing the work.
- `Tessie/*.json` (except `.example.json` templates) is gitignored, real personal
  data — treat as PII, never propose committing it, never paste its contents back to
  the user in full.

## 3. Session continuity log

Keep this section short — a running list of *in-flight* investigations that would
otherwise be lost between sessions. Move resolved items into `AGENTS.md`'s own Bugs /
Requests tables once closed out, and prune this list then.

- **Bunnings Gladesville (PlugShare charger) — RESOLVED**: turned out both PlugShare
  IDs (`801149` and `1517863`) are real, distinct chargers ~70m apart (a public 120kW
  Exploren DC unit, and a patron-only 22kW AC wall outlet) that happen to share the
  exact display name "Bunnings Gladesville". The user ran both live on their own
  machine and got real data for each — the earlier "not found"/empty results seen
  from this sandboxed session were entirely explained by the `api.plugshare.com`
  egress block (§1), not a real 404 or a missing-auth issue; that hypothesis is now
  ruled out. The *actual* bug (BUG-006 in `AGENTS.md`) was in
  `PlugShareRegistry.find_existing_match()`: it matched on exact name key before ever
  checking PlugShare ID, so saving the second station silently overwrote the first
  under the shared key and corrupted its `tariffs`/`keywords` via the
  existing-price-preservation merge in `add_or_update()`. Fixed both functions (ID
  agreement now required before a name/proximity match counts as "the same station";
  a create that would collide on key gets disambiguated instead of overwriting) and
  repaired the live `Tessie/plugshare_chargers.json` by splitting the corrupted single
  entry back into two correct ones, using the field values from the user's own
  live-scraped terminal output as ground truth. Verified with an offline unit test
  (two records, same name, different IDs → both survive) and by re-running
  `--inspect`/`--near Home --dc --radius 15` against the repaired JSON.
- **`--refresh-prices` (added; live-tested by the user, partially working)**: added to
  `find_plugshare_chargers.py` per the user's ask ("prices appear fixed on the ones
  you looked at but missing from a lot of others — could you re-run to pick up all
  prices?"). Root cause: bulk/region search (`--near`/`--state`/`--search`) never
  returns tariff data — only the single-station detail endpoint does. The user ran it
  live: most stations still came back unpriced. Verified via a mocked-network unit
  test that the refresh pipeline itself (missing-detection → fetch → merge → save →
  re-render) is correct, so the failure is almost certainly PlugShare rate-limiting a
  burst of ~20-30 rapid detail requests — manual one-at-a-time lookups (with natural
  pauses) succeed where the batch didn't. Added `PlugShareClient.last_error` (records
  *why* a request failed - HTTP code, timeout, non-JSON/challenge response - instead
  of a bare `None`) and jittered backoff, harder after a likely-429. This makes the
  failure visible and less aggressive but can't force PlugShare's throttling open; see
  BUG-008/TODO-006 in `AGENTS.md`. If the user reports it's still mostly failing after
  this, the next lever is spacing a full refresh across multiple separate invocations
  rather than one big burst (e.g. slicing with `--limit`), not more code cleverness.
- **`find_tesla_chargers.py` "Location / Suburb" column (BUG-007, resolved)**: its
  width floor (14) was narrower than the 18-cell header text, so the header overflowed
  its cell and sat flush against the border whenever no suburb name was long enough to
  push the column wider on its own. Fixed by flooring on `max(header_width, data_width)`
  like `find_plugshare_chargers.py` already did correctly. Verified with a synthetic
  table built from the exact suburb list in the user's own pasted output.
- **Committed — 4 commits landed on `main`** (`6b0e560` table alignment/formatting,
  `9d121a2` PlugShare sort/collision/refresh-prices, `aee3a51` TESLADRIVE removal,
  `ef260f6` README/CI doc audit).
- **Dashcam footage regression (BUG-010, fixed)**: the `aee3a51` TESLADRIVE-removal
  commit above gutted `find_mounted_tesla_volumes()` in `tessie_drives_analyzer.py`
  to an unconditional no-op. Correct for its `"Tessie"` call site (personal data must
  never be read from TESLADRIVE), but that same function is the *only* thing that
  populates `self.teslacam_dirs` (via `find_mounted_tesla_volumes("TeslaCam")` -
  there's no CLI flag override), and locating real footage on mounted TESLADRIVE
  volumes is TESLADRIVE's actual intended purpose. User reported it via a live paste:
  every Saved/Sentry/Recent column showed `·` at every drill-down level (months →
  days → per-trip). Fixed by restoring the real `/Volumes/TESLADRIVE*` scanning
  implementation (verified via git history from before `aee3a51` - it only ever reads
  directory listings, never writes anything, so restoring it doesn't reopen the PII
  issue) and removing the `"Tessie"` candidate-list call site outright rather than
  leaving it merely dead. `tessie_charging_analyzer.py`'s four
  `find_mounted_tesla_volumes()` call sites were checked too - all four pass
  `"Tessie"`/`"invoices"` subdirs (personal data), none are footage-related, so that
  file's no-op gutting was correct as-is and needs no change.
  `find_plugshare_chargers.py` still defines the function but has zero call sites -
  also fine as-is. User confirmed on their real machine: footage is back.
- **Days-menu `[a]ll` just re-showed one day (BUG-012, fixed)**: user's original ask
  to Antigravity was "list drives by month then choose all," but `[a]ll` at the days
  menu chained full per-day interactive sessions instead of showing everything at
  once - looked exactly like selecting the first day and then getting stuck. Added
  `drill_down_all_days()`: flattens every day in view into one combined table with a
  Date column and a single prompt over the whole set. Verified offline with a
  synthetic multi-day fixture (mocked analyzer, piped input) - correct alignment, no
  exceptions.
- **"Parked After" rename + missing in combined view (BUG-013, fixed)**: user found
  this while comparing the new `drill_down_all_days()` (from BUG-012) against the
  original single-day table. Renamed the header to "Parked Duration" (it shows a
  duration, not a time) in `drill_down_day()`, and added the same column - with its
  own gap-to-next-trip logic, careful to only compute a gap when the next trip in the
  flattened list shares the same date, so it never bridges across a day boundary - to
  `drill_down_all_days()`, which had omitted it entirely.
- **`./tessie_places.py review` "No drive logs found" (BUG-011, fixed)**: unrelated
  to the TESLADRIVE work above - a pre-existing path bug. `find_candidate_drive_logs()`
  only checked for `drives_master.csv` directly in each Tessie dir, but
  `consolidate_drives()` actually writes it under a `drives/` subdirectory (with the
  flat layout only as its own fallback) - a mismatch dating back to when the
  master-first ingestion pipeline introduced that subdirectory. Fixed by checking
  `drives/` first then flat, matching `tessie_drives_analyzer.py`'s own fallback
  order exactly. Verified live against the user's real `Tessie/drives/drives_master.csv`
  - `review` now lists unlabelled stop clusters instead of erroring. Also dropped the
  dead `inbox_directory` key from `load_config()`'s default dict - nothing ever read
  it (only an unrelated self-test emoji label in `table_formatter.py` happens to
  mention "Inbox"). Their real `config.json` also had a second dead key
  (`session_csvs_directory`) and a `tessie_directory` set to the long canonical
  iCloud path instead of their preferred `~/iCloud` symlink shorthand - both fixed
  directly in that personal file (gitignored, no commit involved). Also confirmed -
  no code change needed - that the "drop raw CSVs in Downloads or the Tessie dir
  root, they get sorted into drives/ or charges/ on consolidate" workflow the user
  described already exists exactly as they wanted (REQ-004).
- **Clickable Google Maps coordinates in `./tessie_places.py review` (REQ-020,
  done)**: added `hyperlink(text, url)` to `table_formatter.py` (wraps text in an
  OSC 8 terminal hyperlink escape sequence) plus a new `OSC8_REGEX` / `ESCAPE_REGEX`
  so `strip_ansi`/`display_len`/`pad_display`/`format_row`/`truncate_display` all
  correctly treat the wrapper as zero-width - verified this explicitly (hyperlinked
  vs. plain text measure identically, padded/row output stays aligned). Wired into
  both places `review` prints coordinates: the cluster-list table and
  `review_single_cluster()`'s `GPS:` line. Only did the `review` flow since that's
  what the user asked about - `TODO-007` in `AGENTS.md` notes the same helper could
  be reused for other coordinate-printing spots if wanted later.
- **OSC 8 links had no visible styling (BUG-014, fixed)**: user reported not being
  able to see anything clickable for the REQ-020 coordinate links. `hyperlink()` now
  also underlines/colors (cyan) the visible text so it's recognizable as a link even
  where OSC 8 itself renders invisibly; told the user Cmd+Click is the reliable way
  to open these in Terminal.app/iTerm2 on macOS.
- **Ignore list for `review` (REQ-021, done)**: user wanted a way to permanently
  dismiss a stop cluster whose address is fine as-is, instead of it reappearing on
  every `review` run, plus a way to later browse dismissed clusters and either name
  one for real or undo the dismissal. Added `Tessie/ignored_places.json` (gitignored
  via the existing `Tessie/*.json` rule - no `.gitignore` change needed) - a flat
  list of dismissed cluster dicts (same shape as a live cluster: address,
  center_lat/lon, stops, plus radius_m and ignored_at), matched against live clusters
  with the exact same haversine-distance-within-radius rule `places.json` already
  uses, so it slots into `cmd_review_drives()`'s existing filter loop with one added
  line. `review_single_cluster()` gained an `[i]gnore` action (behind a new
  `allow_ignore` param, since the *reviewing an ignored entry* flow shouldn't offer
  to ignore an already-ignored one). New `ignored` subcommand
  (`cmd_review_ignored()`) lists dismissed clusters in the same table style as
  `review`, and lets the user either pick one to name for real (reuses
  `review_single_cluster()` directly on the stored entry, then removes it from the
  ignore list on success) or `u <N>` to un-ignore outright. Verified the full round
  trip live against the user's real data: ignored 2 real clusters, confirmed the
  count dropped in `review` and both appeared in `ignored`, then un-ignored both via
  `u 1`/`u 1` and confirmed `review` was back to the original 41 clusters - net zero
  change to their actual places, but proof the whole thing works end to end.
- **Committed - all of BUG-010 through BUG-014 and REQ-020/REQ-021 landed on `main`
  across 4 commits this session** (footage regression fix; combined all-days view +
  Parked Duration; drives_master.csv path fix + dead config keys; clickable Google
  Maps links + ignore list). Still open: a real architecture concern the user raised
  about `tessie_drives_analyzer.py`/`tessie_charging_analyzer.py` writing
  drives_master.csv/charges_master.csv (and invoices) into the repo's own `Tessie/`
  folder in addition to iCloud, duplicating personal data outside of git's view but
  inside the working tree - user chose "iCloud only, stop writing to the repo" -
  not yet implemented, see next session/turn.
- **Tessie Developer API evaluated (REQ-022, done - not committed yet)**: user asked
  whether the API (https://developer.tessie.com/reference/about) could replace
  manually downloading CSVs, then asked for a test script and a doc so future
  sessions don't re-research this. Added `Tessie/TESSIE_API.md` (auth via
  `?access_token=` query param, generated at https://dash.tessie.com/settings/api;
  `/{vin}/drives` and `/{vin}/charges`; `format=csv` shortcut; open questions around
  pagination/rate limits/exact CSV column parity - not a client library, points back
  to the official reference for anything not covered) and `Tools/test_tessie_api.py`
  (one-shot connectivity/shape check, writes nothing unless `--save-csv`). Answered
  the user's direct question: recommended config key is `"tessie_access_token"` in
  `Tessie/config.json` (added to `Tessie/config.example.json` as a placeholder,
  matching the existing `"vin"` key's flat style) - did NOT add a real value to the
  user's actual `config.json` since they don't have a token yet.
  `api.tessie.com` is blocked by this session's sandbox network allowlist from both
  the cloud container and the device-bridged VM (`403` / `blocked-by-allowlist`,
  same class as PlugShare/Overpass) - confirmed again via a direct `curl` right
  before finishing this work. Could not run the script live from here; instead
  verified its pure logic (token/VIN resolution priority, JSON-shape parsing,
  malformed-response handling) with a mocked-response unit check plus
  `py_compile`. Told the user this needs a real run on their own Mac with a token
  from https://dash.tessie.com/settings/api before trusting anything about the
  live API's actual shape/limits - see BUG-015 in `AGENTS.md`. **Not yet committed**
  - awaiting the user's go-ahead per the standing commit-approval rule in §2 above.
- **Tessie API live-verified by the user (REQ-022 follow-up)**: user ran
  `./Tools/test_tessie_api.py` on their own Mac with a real token and it worked -
  both `/drives` and `/charges` returned 5 real records each with real field names
  (see `Tessie/TESSIE_API.md`'s "Confirmed field names" section for the full lists).
  Key takeaway logged there: these are `snake_case` API fields (`odometer_distance`,
  `starting_saved_location`, `energy_added`, etc.), not the same headers as the
  manual CSV export (`"Started At"`, `"Distance (km)"`, etc.) that `--consolidate`
  parses today - so a real sync tool would need a field-mapping layer, not a
  drop-in CSV swap. `saved_location`/`starting_saved_location`/
  `ending_saved_location` look like a promising shortcut for matching against
  `places.json` without re-geocoding, but that's untested. Still open before
  building anything real on top of this: `format=csv` output hasn't been tried yet,
  pagination/rate limits are untested at scale, and km-vs-mi units on
  `odometer_distance`/`miles_added*` need confirming. **Still not committed.**
- **resolve_place() priority bug fixed (BUG-016)**: user confirmed the direction -
  Tessie's own `Saved Location` is manually entered in their app and can be wrong/
  stale, and the API has no write endpoint for it (confirmed against the official
  docs), so the only viable fix is one-directional: stop trusting Tessie's text over
  our own registries, don't try to push corrections back into Tessie. Reordered
  `resolve_place()` in `tessie_drives_analyzer.py` so GPS-geofence matching against
  `places.json`/`charging_places`/`supercharger_places` runs first (it was already
  commented "Highest Accuracy!" but ran *after* the Tessie saved-location check,
  which returned early whenever Tessie had any saved-location text at all -
  effectively dead-coding the accurate GPS match most of the time). Tessie's
  saved_location is now only a fallback for when no GPS data is available. Verified
  with a synthetic conflict case (GPS sitting exactly on a known place, deliberately
  wrong saved_loc string) - registry now wins; also verified the no-GPS fallback
  path still returns the Tessie text as before. **Not yet committed.**
- **CSV-vs-API comparison done, zero gaps found (REQ-022 closed out)**: first
  `--save-csv` attempt hit an HTTP 500 on both endpoints (BUG-017) - root-caused to
  the script's hardcoded `Accept: application/json` header clashing with a
  `format=csv` request; fixed by sending `Accept: text/csv` for CSV requests.
  Fetching the official parameter docs for both endpoints while investigating also
  fully resolved the earlier units/timezone open question: the API defaults to
  mi/UTC, but `distance_format=km&temperature_format=c&timezone=Australia/Sydney`
  matches this repo's CSV convention exactly - added as the script's defaults.
  User re-ran `--save-csv` after the fix and it worked; read the resulting
  `Tessie/api_test_samples/sample_drives.csv` / `sample_charges.csv` directly via
  `device_bash` (no need to have them pasted) and diffed headers programmatically
  against the real `Tessie/drives/drives_master.csv` /
  `Tessie/charges/charges_master.csv`. Result: drives is a byte-identical 26/26
  column match (Tessie's own CSV formatter already computes `Duration (Minutes)`
  and `Average Energy Used (Wh/km)` server-side - no local derivation needed);
  charges has all 17 manual columns in the same order plus 4 bonus columns
  (`Distance Since Last Charge`, `Max Range`, `Max Ideal Range`, `Capacity`) not in
  the manual export. **No real gaps** - the API is not missing anything the manual
  download provides. This clears the way for a real sync tool to replace manual
  downloads entirely, whenever the user wants to build one - not yet started.
- **Row-level verification, not just headers (2026-09-06)**: user asked to compare
  against the REAL master files at the canonical iCloud location
  (`/Users/glenn/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie`, not the
  repo's own possibly-stale copy) - requested and was granted folder access via
  `device_request_folder_access` (the plain `~/iCloud/...` symlink path was refused;
  the canonical `Library/Mobile Documents` path worked). That real location has 1055
  drive rows (2026-06-01 to 2026-09-06) and 197 charge rows - much more history than
  the repo's copy, confirming they really are two separate, drifting copies (still
  unfixed - see the "iCloud only" note earlier). User then re-ran the script with
  `--since-days 14 --limit 50 --save-csv`; matched all 50 API drive rows and 31 API
  charge rows against the real master by `Started At (AEST)` timestamp and diffed
  every shared column value-for-value (not just presence). Result: 100% of drives
  matched by timestamp, every field identical except Saved Location; charges matched
  27/31 by timestamp (identical fields again) with the other 4 simply newer than the
  real master's last recorded charge (proof the manual file is already stale, not a
  script bug). The Saved Location differences are expected per BUG-016 - the master
  file is a frozen snapshot, the API reflects Tessie's current (mutable) app labels.
  This is now a genuinely proven, row-verified conclusion, not just a header/shape
  comparison - logged in full in `Tessie/TESSIE_API.md`.
- **"chargers" -> "charges" rename requested (TODO-008), scope unclear - asked
  before touching anything**: user wants every "chargers" reference renamed to
  "charges" repo-wide (code, filenames, directories, docs), reasoning it's about
  charging the car, not physical hardware. Flagged a real conflict before doing
  anything: several current "charger(s)" names genuinely mean physical
  hardware/station registries (`find_tesla_chargers.py`, `find_plugshare_chargers.py`,
  `Tessie/tesla_chargers.json`, `Tessie/plugshare_chargers.json`,
  `Tessie/personal_chargers.json`, `Tessie/tesla_superchargers.json`) - a distinct
  concept from a charging *session* (`charges_master.csv`,
  `tessie_charging_analyzer.py`). Renaming the hardware registries to "charges"
  would conflate two things `resolve_place()` (BUG-016 above) and the charging
  analyzer both currently treat as separate on purpose. Asked the user via
  AskUserQuestion to clarify exact scope before starting any rename.
- **Real sync tool built (REQ-023), separate from the analyzer scripts as asked**:
  user confirmed the JSON-for-bookkeeping + CSV-for-the-actual-file design, then
  asked for fetching to be one shared thing rather than baked into
  `tessie_drives_analyzer.py`/`tessie_charging_analyzer.py` separately. Built
  `Tools/tessie_api_common.py` (shared config/token loading + the HTTP call,
  used by both this and the earlier `test_tessie_api.py`, refactored to match)
  and `Tools/tessie_api_sync.py` (the actual sync tool, driven by a small
  `DATA_TYPES` table so adding another Tessie endpoint later is one new entry,
  not a new script). It fetches JSON into a local `Tessie/api_cache/<type>.json`
  cache keyed by the API's own record `id` (idempotent re-runs, incremental
  syncing via a `last_synced_to` watermark with a 1-day overlap for late
  edits), and - only when something's actually new or changed - fetches the
  *same* window as `format=csv` and writes that straight to the landing
  directory unchanged, rather than hand-deriving CSV columns from JSON (a real
  rounding mismatch was found in `Cost Per kWh` when checking this - Tessie
  computes it server-side from full-precision values before rounding, so
  re-deriving from already-rounded JSON fields can disagree; using Tessie's own
  CSV output sidesteps that class of bug entirely). The written CSV is picked
  up by the EXISTING `--consolidate`/`auto_ingest_from_landing()` flows in both
  analyzer scripts unchanged - confirmed by reading their landing-dir-scanning
  code first (schema-detected by column presence, not filename, so drives- and
  charges-shaped files sort themselves out safely).
  No pagination cursor is documented for either endpoint, so a JSON fetch that
  might be truncated by `limit` is split in half and re-fetched recursively.
  Caught a real bug in my own first version of this while writing mocked unit
  tests (not from the user): the original code trusted a chunk as "complete"
  once the date window couldn't be split further, even if it was still sitting
  exactly at the limit ceiling - silently returning truncated data in a
  pathological-density edge case. Fixed to raise loudly instead once a window
  can't split further while still at the limit (this should never happen for
  real drive/charge data - would mean 500+ events inside one hour). Tested 9
  scenarios with a fake `api_get`: fresh sync, idempotent no-op re-run,
  detecting a changed record, `--dry-run` truly read-only, `--reset-cache`,
  chunking recovery at realistic density, the pathological-density raise,
  CSV/JSON row-count mismatch warning (still saves, just warns), and clean
  HTTP-error surfacing.
  Also caught and fixed a real gitignore gap while testing: `Tessie/*.json`
  does NOT match a nested `Tessie/api_cache/*.json` (gitignore glob semantics -
  `*` doesn't cross `/`), so the new cache directory (real personal telemetry)
  would NOT have been excluded from git - added `Tessie/api_cache/` to
  `.gitignore` before this tool could ever produce a real cache file.
  Ran `--dry-run` live via `device_bash` to confirm the plumbing up to the
  network boundary: correctly loaded token/VIN/window, failed exactly at the
  blocked `api.tessie.com` call as expected (BUG-018) - same restriction as
  always, nothing new. **Not yet run against the live API** - needs the user to
  actually try it. **Not yet committed** - this session now has quite a lot
  accumulated (resolve_place fix, TESSIE_API.md, test script, sync tool,
  .gitignore fix, config.example.json) awaiting a commit-message discussion.
- **Sync tool now auto-consolidates too (REQ-024)**: user pushed back on requiring a
  separate manual `--consolidate` step after syncing - "the script should already
  know to consolidate," and "the analyzer scripts don't need to worry then." Agreed
  this was the right call once I checked how the analyzer scripts actually expose
  consolidation: both `tessie_drives_analyzer.py` and `tessie_charging_analyzer.py`
  already have a `--consolidate` CLI flag that does everything downstream (for
  charges, that flag's own code path already calls `load_charges()` ->
  `auto_ingest_from_landing()` -> `reconcile()` -> `consolidate_charges_master()`
  internally, so nothing new needed there). Added `run_consolidate()` to
  `tessie_api_sync.py`, which shells out to that exact command as a subprocess
  (`[sys.executable, <script>, "--consolidate"]`) rather than importing either
  script's classes - keeps the analyzer scripts completely unchanged and decoupled
  from how fetching works, while giving the user one command that does everything.
  Interactive runs get a y/N prompt (`should_consolidate()`) before merging;
  non-interactive runs (e.g. a future cron job) refuse to auto-consolidate unless
  `--yes` is also passed - deliberately opt-in for automation, not a silent default,
  since this would be writing into the user's real master CSVs. Added
  `--no-consolidate` as an escape hatch back to the original fetch-only behavior.
  Tested with mocks (subprocess and input() both injectable): no-pending-skip,
  `--yes` bypasses the prompt even non-interactively, non-tty without `--yes`
  refuses with a clear reason, interactive yes/no answers, and `run_consolidate()`
  building the exact right command per type and surfacing a non-zero exit code as
  failure. Re-ran the full previous mocked test suite too - no regressions.
  **Not yet run live** (this part), **not yet committed**.
- **BUG-019: sync cache advanced on fetch success alone, not on safe merge (2026-09-07)**:
  after a real sync left CSVs sitting in `~/Downloads` at the consolidate prompt,
  user asked a sharp question - "this would not work if the original csv's were
  deleted?" Reproduced it first rather than answering from assumption: mocked
  `sync_one_type()` fetching 5 fake records, deleted the resulting CSV before ever
  running `--consolidate` on it, re-ran with identical data - got back
  `new_or_changed: 0, csv_path: None`, confirming those 5 records were now
  permanently unrecoverable. Root cause: the cache's `_meta.last_synced_to`
  watermark (and the per-record "seen" bookkeeping) was being saved unconditionally
  on any successful fetch, before `--consolidate` ever merged the data anywhere -
  "fetched from the API" and "durably merged into the master file" were wrongly
  treated as the same guarantee. **Fixed**: `sync_one_type()` no longer saves the
  cache itself when a CSV is pending consolidation - it commits immediately only
  when `new_or_changed == 0` (nothing written, nothing at risk), otherwise it hands
  the in-memory cache back via a `pending_commit` key. New `commit_cache(spec,
  cache, to_ts)` does the actual save. `main()` now calls `commit_cache()` per type
  only after that type's `--consolidate` subprocess actually returns success; a
  declined/skipped/failed consolidate leaves the cache uncommitted on purpose, so
  the next sync safely re-fetches and re-surfaces the same window instead of losing
  it (worst case: a re-fetch, which the existing content-key dedup in
  `--consolidate` already handles). Verified with a 15-check mocked reproduction
  proving the exact original scenario is now fixed, plus a full regression re-run
  of every previously-passing test for this file (chunking at realistic/pathological
  density, all `should_consolidate`/`run_consolidate` branches) and a live
  `--help`/`py_compile` check on the real script. No real data was ever actually
  lost - the user's 4 real synced CSVs were still sitting untouched in
  `~/Downloads` the whole time; this was caught by his question and a reproduction
  test, not by an actual loss incident. Documented in `Tessie/TESSIE_API.md` and
  `AGENTS.md` (BUG-019). **Not yet committed** - same accumulated batch as before
  (resolve_place fix, TESSIE_API.md, test script, sync tool incl. this fix,
  .gitignore fix, config.example.json) awaiting a commit-message discussion. The
  4 real pending CSVs in `~/Downloads` are still unconsolidated - user hasn't
  answered the `--consolidate` prompt for them yet.
- **REQ-025: CSV filenames now named after the data, not the fetch clock (2026-09-07)**:
  user suggestion right after BUG-019 - name the written CSV after the last entry the
  fetch actually found (e.g. `..._20260907_085640.csv` reflecting when the *data* is
  from), not `datetime.now()` at the moment the sync happened to run, plus a padding
  suffix (`_01`, `_02`, ...) so a repeated stamp never clobbers an existing file. Added
  `latest_entry_timestamp(records)` (max of each record's `ended_at`, falling back to
  `started_at`) and `unique_csv_path(landing_dir, prefix, stamp)` (plain name first,
  then `_01`/`_02`/... only if that name is already taken). User also asked directly
  whether clobbering is even possible given the BUG-019 cache fix - answered yes, and
  explained why: the cache fix stops the *watermark* from silently swallowing data, but
  two different still-unconsolidated fetches can still land on the identical "latest
  entry" stamp - either a field-level edit to an already-seen record (doesn't move the
  max timestamp - the exact `Distance Since Last Charge` case found live earlier) or,
  by BUG-019's own design, simply re-running the sync repeatedly before ever answering
  the `--consolidate` prompt, which re-fetches the same pending window (and computes the
  same stamp) every time until you consolidate. Verified with 13 mocked checks including
  the exact BUG-019 delete-and-recover scenario re-run with the new naming, and a second
  variant that does NOT delete the first file to prove the second pending run gets `_01`
  instead of overwriting it. Full existing suite re-run alongside - no regressions.
  Documented in `Tessie/TESSIE_API.md` and `AGENTS.md` (REQ-025). **Not yet committed** -
  same accumulated batch as before. The user's 4 real pending CSVs in `~/Downloads` (from
  before this naming change) are still unconsolidated and use the old wall-clock naming;
  this only affects CSVs written from here on.
- **BUG-020: `tessie_api_sync.py` had no `--consolidate` flag of its own (2026-09-07)**:
  user ran `./Tools/tessie_api_sync.py --consolidate` (trying to merge the 4 CSVs left
  pending from the earlier real sync) and got `unrecognized arguments: --consolidate` -
  the sync tool only had `--yes`/`--no-consolidate`, while `--consolidate` is the
  analyzer scripts' own flag name. Fixed by adding a real `--consolidate` flag to
  `tessie_api_sync.py` that skips fetching entirely (no token/VIN needed) and runs
  `run_consolidate()` directly for the selected type(s) - same subprocess call as typing
  each analyzer's own `--consolidate`, just from one place. Rejects `--no-consolidate`/
  `--dry-run` combos with exit code 2. Tested with 7 mocked/subprocess checks. **Then got
  this wrong once**: ran it for real via this session's own sandboxed shell and reported
  "Consolidated 1055 total drives (+0 new)" / "appended 0 new charges (Total: 197)" as
  confirmation the pending CSVs were redundant - a false negative, not a real result.
  Root cause: `~/Downloads` (from `Tessie/config.json`) expands inside that shell to ITS
  OWN home directory, which has no `Downloads` folder at all - the real
  `/Users/glenn/Downloads` only exists there via a `~/mnt/Downloads` mount alias that a
  subprocess launched from `tessie_api_sync.py` can't see. So the analyzer scripts found
  zero landing files to ingest; "+0 new" meant "0 files processed", not "0 new among
  files genuinely processed", and nothing was archived because nothing was read. The user
  then ran the real command himself and got the true result: "Ingested & Archived" for
  all 4 pending files, "Consolidated 1058 total drives (+3 new)", "Successfully appended
  4 new charges (Total: 201)" - genuinely new data, correctly merged and archived. That
  run is authoritative; the earlier "safe to delete, fully redundant" claim was wrong and
  has been corrected in `Tessie/TESSIE_API.md` and `AGENTS.md` (BUG-020). **Lesson
  captured for future sessions**: never treat a `~`-relative path resolved inside this
  session's own device-bridge shell as equivalent to the real path on the user's machine
  - verifying real user-facing behavior for anything reading a `~`-relative config value
  needs the user to run it themselves, not a sandboxed stand-in. **Not yet committed** -
  same accumulated batch. The user's 4 real pending CSVs are now genuinely, correctly
  consolidated and archived on his machine - nothing further needed there.
- **BUG-021: `resolve_location()` (charging analyzer) matched "first entry reached", not
  "best entry" (2026-09-07)**: user's `--third-party` output showed a real Bunnings
  Gladesville charge (confirmed by exact GPS + Tessie's own saved_location) displayed as
  "Emirates One & Only Wolgan Valley" (~150km away) and a separate no-GPS Bunnings charge
  as "Bunnings Bellambi" (Wollongong). Root cause: the function checked each registry
  entry in sequence (saved-loc exact-or-keyword match, then GPS radius, then address
  keyword match) and returned on the FIRST tier hit for the FIRST entry reached in JSON
  order - and PlugShare-scraped keyword lists include generic words with zero
  discriminating power ("Bunnings" on 6 stores, "Australia" on nearly every entry), so an
  early, unrelated entry sharing only a generic word could win over the correct station's
  own exact GPS/name match sitting later in the same registry. Confirmed the correct
  "Bunnings Gladesville" entry sits ~28m from the real coordinates (inside its own 150m
  radius) and matches the saved-location string exactly - it should always have won.
  Fixed by rewriting resolve_location() to rank matches by GLOBAL priority across every
  registry: GPS radius (nearest wins, charging-registry entries preferred over a
  same-location places.json duplicate that carries no network name), then exact
  saved-location name match, then keyword-substring match restricted to keywords that are
  both ≥5 chars AND globally unique across the whole combined registry (one memoized
  frequency count) - so shared generic words can never win, while genuinely distinguishing
  keywords still can. Verified with 9 checks against the real registry files and the real
  charges' own coordinates from charges_master.csv: both misresolved sessions now
  correctly resolve to Bunnings Gladesville/Exploren, all 5 already-correct Supercharger
  sessions still resolve correctly, and the places.json duplicate no longer steals the
  network label. Documented in Tessie/TESSIE_API.md and AGENTS.md (BUG-021). **Not yet
  committed** - same accumulated batch. User should re-run `--third-party` himself to see
  the corrected output (didn't re-verify the full report end-to-end from this session's
  own sandbox, on purpose, after the BUG-020 path-resolution mistake - verified the fix
  directly against the real registry data and real coordinates instead, which doesn't have
  that trap).
- **TODO-009: timezone handling needs to be UTC-anchored (2026-09-07)**: user raised, mid-turn
  while BUG-021 was being investigated, that charging location matters for local time and a
  single fixed timezone breaks down across a real border crossing (NSW then SA 30 min later
  - different real UTC offsets) - "UTC is king." Verified rather than assumed: the API's
  from/to params and the JSON started_at/ended_at fields are true Unix-epoch UTC (unaffected
  by the timezone request param, which only affects Tessie's own CSV text formatting), so
  tessie_api_sync.py's JSON cache is already correct. The real bug is downstream: every CSV
  (manual AND API-fetched) has its date columns pre-converted to one fixed Australia/Sydney
  zone before we see them, and both analyzer scripts parse/sort/display those strings with
  no timezone awareness - so an SA event gets a wrong displayed local time, the static
  "(AEST)" header is inaccurate about half the year during daylight saving even for genuine
  Sydney events, and naive string/datetime comparison across a DST fall-back transition
  could misorder events twice a year even with no travel involved. Diagnosed and logged as
  TODO-009 in AGENTS.md and Tessie/TESSIE_API.md with a recommended direction (UTC-anchored
  storage, per-event GPS-derived local display with an explicit numeric offset) - NOT yet
  implemented; this is an architecture-level change (CSV schema + both analyzers' parsing/
  sorting/display) that needs a scoping discussion before starting, not a quick patch.
- **BUG-022: third-party place/network trusted Tessie's own guess over the paid invoice
  (2026-09-07)**: user's `--third-party` output showed all 3 sessions as "Bunnings
  Gladesville", but invoice 0000533887's own filename/printed content says "Bunnings
  Rydalmere" - a different store. Root cause: a matched session's displayed place/network
  was computed once at charge-load time from Tessie's own Saved Location CSV field, never
  revisited even after reconciliation had already identified which invoice paid for it -
  so the invoice's own printed (and more trustworthy) station name was never used for
  display. User's own suggested design: since an invoice always prints a literal, correct
  station name, record it against the invoice number, persisted beside the invoice files.
  Fixed by implementing exactly that: new resolve_invoice_location() re-resolves place/
  network/emoji from a MATCHED invoice's own location text once reconciliation has
  identified it; new invoice_locations.json persisted beside the invoice files as a
  hand-correctable invoice-number -> place/network/emoji map (a "manual_override": true
  entry is never recomputed); the invoice's own printed network is now trusted over
  resolve_location()'s guess for network specifically, since a GPS/keyword match landing
  on a plain places.json entry has no operator name of its own and used to fall back to a
  generic placeholder like "DC Fast". Two follow-on bugs caught during verification
  (same transparency as BUG-020/BUG-021, both fixed before being reported as done): (1)
  the fix first mis-resolved the Gladesville sessions to "The Ville Resort Casino" - the
  keyword "Ville" matched as a bare substring inside "gladesVILLE" - fixed with
  word-boundary regex matching; (2) that fix then under-matched the same sessions to a
  generic "Gladesville" fallback because plugshare_chargers.json has two duplicate dict
  entries for the same real store, so the correct keyword phrase failed a raw "unique
  count == 1" check - fixed by counting keyword uniqueness by distinct NORMALIZED display
  name (stripping a trailing "(...)" suffix) instead of raw entry count. Verified against
  the real invoice PDFs/registries: all 3 third-party sessions now resolve correctly
  (0000533887 -> Bunnings Rydalmere/Exploren, 0000545157/0000555286 -> Bunnings
  Gladesville/Exploren), all 5 Supercharger sessions unaffected, and invoice_locations.json
  was written correctly for all 8 loaded invoices. Documented in Tessie/TESSIE_API.md and
  AGENTS.md (BUG-022). **Not yet committed** - same accumulated batch.
- **Answered, not a bug: "a lot of place names are now missing from drives_master.csv"
  (2026-09-07)**: user's concern, addressed without needing a code change. Checked the
  real drives_master.csv: 702 of 1058 rows have a blank Starting/Ending Saved Location -
  but that column is Tessie's own raw field, populated only when an address has actually
  been tagged as a saved location in the Tesla/Tessie app; most one-off stops (a shop, a
  friend's street) never get tagged, so a majority being blank is expected, not a
  regression (drives_master.csv isn't tracked in git, so there's no history to diff
  against, but the blank rows sampled were exactly the kind of address that would never be
  tagged). tessie_drives_analyzer.py's review output never reads that raw field for
  display anyway - it calls resolve_place() live from GPS + address text against the
  registries (the BUG-016 GPS-priority logic), independent of whether Tessie tagged it.
  tessie_places.py's whole purpose is the mirror image: cluster exactly those untagged/
  unmatched GPS stops from drives_master.csv so the user can interactively name the
  frequent ones - working on the blank rows IS its job. No fix needed; flagged to the user
  as informational.
