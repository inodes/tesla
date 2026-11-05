# Load speed: notes and numbers

The task is docs/LOAD_SPEED_TASK.md. Work happens on branch `claude/load-speed` (cut from `merged`), pushed after every commit, with a small PR per step into `merged`.

## Handoff

- **State**: all seven steps of docs/LOAD_SPEED_TASK.md are done on branch `claude/load-speed` (PR #17 into `merged`). Commits, oldest first:
  `25e635e` fixture + harness + baseline · `0aec053` page timings in the harness · `adafa01` MapLibre vendored, `<head>` non-blocking · `78dfbcd` ETag/gzip for
  the page files · `1b30eed` parallel `boot()` · `c96b159` ETag/gzip for /api JSON · `abe5d39` minute_kind rebuild loop · `1f683af` Drives list kept with its
  signature · `67f6ad4` one volume look for every role · `173ec36` `_warm` measured, left as it was · `4bdc113` `clip_clock.utc` kept per stamp · `814a8d1`
  provenance read per file · `904232e` ShortPlace fed only on change · `5e08d8b` /api/teslacam and /api/clips from what was kept · `4f95b52` perf_log +
  POST /api/perf · `b4d3f79` drive window: Recent times once · then this file's final table.
- **Next leads (not done)**:
  - `/api/clips/keep`: `ClipKeep.status` waits for its first build after every start (2.6-9.5 s on the fixture; kept in memory only). The TeslaCam page draws the
    clips first and fills the keep column after, so the list is not held, but the column is. Keeping its last look on disk (as the Drives list now is) is the
    next step; care: after a delete the page reads /api/clips/keep with a plain fetch (no `fresh=1`), so a kept copy must be dropped by ClipKeep's own changes
    (`_settle`, `invalidate`).
  - `_warm`: an earlier start made the second page slower (measured); a warm-up that yields to page requests could still help, not tried.
  - The "Load failed" after "Reading the drive…" on JOWUA (task doc) was not reproduced on the fixture; with /api/clips and /api/teslacam answered from what was
    kept and the volume look no longer repeated per role, the long request that likely dropped is gone, but this is not proven: check tesla_perf.log / tesla_app.log.
- **Sandbox notes**: cdnjs is blocked here; the vendored files are `dist/` of the npm package `maplibre-gl@4.7.1` (the same files cdnjs serves). Playwright's
  headless shell is missing here; perf_run.py falls back to `/opt/pw-browsers/chromium` (or `PERF_CHROMIUM`). Times vary run to run with what the background
  keeper is doing (it reads the fake drives at start); compare several runs before trusting a small difference.
- **Pre-existing test failures** (before any change here, also alone): `test_snapshots_for.SnapshotsFor.test_only_drives_over_the_clips_without_snapshots`,
  `test_timeline_stored.SentryWindow.test_the_minutes_of_a_sentry_event_make_a_window_with_telemetry`. Every commit kept these two and no others.
- **Found, not from this work**: running the existing test suite creates `~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/PV` in the home folder (it was there
  after the first suite run, before any change here); not looked into.

## Before / after (harness, fresh fixture each, Linux sandbox, keeper running)

Baseline: commit `25e635e` with page timings (`0aec053` harness); after: `b4d3f79`. Times vary between runs (see the per-change tables below for repeated measurements).

| | first start | restart |
|---|---|---|
| process start → first answer | 10.97 s → 3.09 s (other runs after `67f6ad4`: 1.04, 1.53 s) | 0.62 s → 0.39 s |
| first paint, #/activity / #/drives / #/places / #/teslacam (ms) | 508 / 376 / 400 / 336 → 192 / 176 / 164 / 204 | 396 / 412 / 372 / – → 212 / 160 / 156 / 220 |
| page rendered (ms) | 646 / 421 / 502 / 359 → 310 / 300 / 318 / 319 | 433 / 450 / 425 / 349 → 298 / 262 / 266 / 344 |
| /api/teslacam cold / warm | 9.547 / 2.787 s → 1.366 / 0.004 s | 8.284 / 5.254 s → 0.004 / 0.004 s |
| /api/clips cold / warm | 2.621 / 2.266 s → 1.515 / 0.045 s | 0.405 / 2.208 s → 0.022 / 0.010 s |
| /api/clips/keep cold | 0.017 s → 4.885 s (see Next leads: now asked before the background build is done) | 0.014 s → 9.512 s (same) |
| /api/sentry cold | 0.672 s → 0.021 s | 0.952 s → 0.020 s |
| bytes on the wire: app.js / drivelist / chargers / activity | 464,205 / 1,579,134 / 2,043,182 / 942,404 → 160,107 / 305,401 / 299,210 / 194,425 | same |
| drive page /api/teslacam/auto/window (5 drives) | 0.43-3.26 s → 0.07-0.56 s | |
| server CPU in the 30 s after a restart | 18.0 s → 7.2 s | |

## What to check on the Mac (cannot be measured here)

Run the app normally for a day, then read `~/Library/Logs/tesla_perf.log` (trim nothing private: it holds routes, API paths, times and sizes only):

```
awk -F'\t' '$2=="start"' ~/Library/Logs/tesla_perf.log | tail -12     # since_launch_s at 'setup done' / 'server bound' / 'first request'
awk -F'\t' '$2=="page"' ~/Library/Logs/tesla_perf.log | tail -30      # ms per page opened; first_ms = window open to first page drawn
awk -F'\t' '$2=="slow"{split($4,a,"=");print a[2]"\t"$3"\t"$5}' ~/Library/Logs/tesla_perf.log | sort -rn | head -20   # the slowest /api requests: seconds, path, bytes
```

Good: `server bound` within ~1 s of launch, `first_ms` under ~1,500, no `slow` line for /api/teslacam or /api/clips over 1 s. Also check by eye:
- the maps (Places map view, a drive's map) still draw, with the network off too (MapLibre is local now; the map tiles still need the network);
- after a delete or move on the TeslaCam page the list shows the new state (it asks with `fresh=1` for a minute after an action);
- a drive plugged in shows on the TeslaCam page within a few seconds (the volumes are looked at behind; the page asks again while the answer is stale);
- what `zsh -lc` (the launcher's login shell) adds: the time between launching Tesellate and `since_launch_s` of `setup done` is not seen by the app.

## Per-change measurements (fixture, Linux sandbox; page times in headless Chromium, new page, empty cache)

| change | route | first paint before → after | rendered before → after |
|---|---|---|---|
| MapLibre local + deferred (first start) | #/activity | 508 → 264 | 646 → 370 |
| | #/drives | 376 → 256 | 421 → 377 |
| | #/places | 400 → 204 | 502 → 326 |
| | #/teslacam | 336 → 156 | 359 → 267 |
| (restart) | #/activity | 396 → 132 | 433 → 261 |
| | #/places | 372 → 184 | 425 → 302 |

| change | file | bytes on the wire before → after | first load ms before → after | repeat load |
|---|---|---|---|---|
| ETag + gzip (primed at start) | /app.js | 464,205 → 159,668 | 1.4 → 1.4 | 200 (0.8 ms) → 304 (1.0 ms) |
| | /app.css | 39,318 → 9,958 | 0.8 → 0.9 | 200 → 304 |
| | /vendor/maplibre-gl.js | 803,086 → 211,500 | 1.6 → 0.8 | 200 → 304 |
| | / | 1,720 → 740 | 11.5 → 14.0 (first request on a new connection either way) | 200 → 304 |

| change | route | rendered ms before → after (median of 5, restart, headless Chromium) |
|---|---|---|
| boot(): settings asked in parallel | #/activity | 352 → 322 |
| | #/drives | 361 → 304 |
| | #/places | 381 → 281 |
| | #/charges | 361 → 317 |

| change | list | bytes on the wire before → after | server time on localhost (after start-up work settled, median of 7) |
|---|---|---|---|
| JSON ETag + gzip level 1 | /api/drivelist | 1,579,134 → 323,745 | no gzip 69 ms, gzip 45 ms, 304 134 ms |
| | /api/chargers | 2,043,182 → 299,210 | 29 / 35 / 34 ms |
| | /api/activity | 942,404 → 194,425 | 35 / 40 / 30 ms |
| | /api/places | 192,182 → 28,271 | 41 / 37 / 11 ms |

| change | measure | before → after |
|---|---|---|
| minute_kind save only when new + Drives list kept with its signature | server CPU in the 30 s after a restart (`--no-background`) | 18.0 s → 7.2 s |
| | /api/drivelist right after a restart | 0.061 s (stale copy, rebuild behind) → 0.041 s (current copy, no rebuild) |

| change | measure | before → after |
|---|---|---|
| one volume look for every role, shared by the first askers | first start: process start → first answer | 9.41 / 10.38 / 10.97 s (3 runs) → 1.53 s |
| | restart: process start → first answer | 0.52-0.62 s → 0.30 s |
| | /api/teslacam cold / warm, first start | 5.50 / 5.97 s → 0.93 / 0.05 s |
| | /api/teslacam cold / warm, restart | 1.70 / 11.76 s → 0.06 / 0.07 s |

| change | measure | before → after |
|---|---|---|
| `clip_clock.utc` kept per stamp | /api/clips warm, 7 calls after 20 s, `--no-background` | 0.215-0.465 s, median 0.235 → 0.155-0.316 s, median 0.203 |

| change | measure | before → after |
|---|---|---|
| provenance read per file (only changed files) | `_skews_refresh` after one of 148 files changed (10 runs) | median 60.1 ms → 2.9 ms |

| change | measure | before → after |
|---|---|---|
| ShortPlace fed only when its inputs change | `_short_where` right after a clock-version change (8,673 minutes, 10,707 places) | median 20.1 ms → 0.0 ms |

| change | measure (harness, fixture, keeper running) | baseline → after |
|---|---|---|
| /api/teslacam, /api/clips answered from the kept answer, refreshed behind | /api/clips cold / warm, first start | 0.791 / 1.763 s → 0.067 / 0.127 s |
| | /api/clips cold / warm, restart | 0.746 / 0.842 s → 0.104 / 0.045 s |
| | /api/teslacam cold / warm, restart | 1.698 / 11.759 s → 0.019 / 0.017 s |
| | /api/teslacam cold, first start (nothing kept yet: the one look is waited for) | 5.502 s → 2.988 s |

| change | measure (5 drives of the fixture, `--no-background`, after 20 s) | before → after |
|---|---|---|
| Recent minutes' times worked out once per window, not once per Sentry event | /api/teslacam/auto/window cold | 3.26 / 2.85 / 0.43 / 2.52 / 0.46 s → 0.56 / 0.10 / 0.10 / 0.14 / 0.07 s |
| | warm | 2.58 / 2.32 / 0.46 / 2.95 / 0.46 s → 0.10 / 0.10 / 0.08 / 0.12 / 0.07 s |

The JSON times are within the noise of the background builds: no time gain or loss shows on localhost; the gain is bytes (5-8x less). Level 1 rather than 6: a 1.5 MB list compresses in 11 ms instead of 34 ms (measured), for 324 KB instead of 242 KB.

On localhost the time is the same: the gain is bytes, and a reload that does not change a file gets a 304. Without priming, the first gzip of app.js took 23 ms (measured), which is why the files are compressed in the background at start.

In the sandbox cdnjs is refused at once; on a Mac with a slow or no network the old `<head>` waited for cdnjs before the first paint (up to the network timeout offline), so the gain there is larger and is not measured here.

## How to measure

```
TESLA_CONFIG=config.example.json python3 Tools/maintenance/make_perf_fixture.py /tmp/fx        # about 25 s, 600 MB (mostly the tiny clip files' blocks)
python3 Tools/maintenance/perf_run.py /tmp/fx --json /tmp/fx_run.json                          # about 1 min
```

The fixture (seed 7, 150 days): 996 drives, 174 charges, 846 idles, 6,000 charging stations, 40 saved places, 9,142 recorded minutes with telemetry,
provenance and footage-log entries, 324 Saved/Sentry events; TeslaCam drives under FIXTURE/home/mnt: Archive 57,754 files, JOWUA 25,648, Tesla USB 15,726.
The harness runs the server with HOME=FIXTURE/home, so its caches and logs go there, not to the real home. The run changes the fixture (kept lists,
the keeper's logs): make a fresh fixture for numbers that are compared. Linux sandbox, 4 cores, warm file cache; the background keeper runs.

## Baseline (2026-11-05, commit before any change)

First start (computer's cache emptied): bound 0.24 s, first answer 10.38 s. Restart: bound 0.26 s, first answer 0.52 s.

| path | first start cold s | warm s | restart cold s | warm s | bytes |
|---|---|---|---|---|---|
| / | 0.008 | 0.005 | 0.002 | 0.001 | 1,766 |
| /app.js | 0.009 | 0.005 | 0.001 | 0.001 | 464,205 |
| /app.css | 0.004 | 0.003 | 0.001 | 0.001 | 39,318 |
| /api/status | 0.017 | 0.011 | 0.002 | 0.002 | 194 |
| /api/summary | 0.003 | 0.002 | 0.001 | 0.001 | 198 |
| /api/activity | 0.009 | 0.006 | 0.015 | 0.002 | 942,404 |
| /api/drivelist | 5.374 | 0.087 | 0.028 | 0.025 | 1,488,600 |
| /api/places | 0.109 | 0.004 | 0.005 | 0.006 | 192,182 |
| /api/alerts | 0.034 | 0.006 | 0.036 | 0.028 | 62 |
| /api/chargers | 0.240 | 0.020 | 0.166 | 0.047 | 2,043,182 |
| /api/battery | 0.008 | 0.015 | 0.028 | 0.002 | 8,772 |
| /api/analytics | 0.043 | 0.016 | 0.002 | 0.002 | 1,144 |
| /api/teslacam | 5.502 | 5.965 | 1.698 | 11.759 | 149 |
| /api/clips | 0.791 | 1.763 | 0.746 | 0.842 | 555,396 |
| /api/clips/keep | 0.045 | 0.087 | 5.455 | 0.012 | 630,478 |
| /api/sentry | 0.075 | 0.908 | 0.028 | 0.006 | 212,007 |
| /api/keeper, footage/todo, review/scanned, drivereview/all, clipclock, tessie, settings | under 0.07 | | | | small |

The times vary with what the background keeper is doing at the moment (it reads the fake drives at start), which is why some warm numbers are higher than cold ones.
