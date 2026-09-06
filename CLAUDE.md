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
