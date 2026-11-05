# Load speed: notes and numbers

The task is docs/LOAD_SPEED_TASK.md. Work happens on branch `claude/load-speed` (cut from `merged`), pushed after every commit, with a small PR per step into `merged`.

## Handoff

- **Done** (all on PR #17): step 1 `25e635e` fixture + harness + baseline, `0aec053` harness page timings in Chromium; step 3 `adafa01` MapLibre 4.7.1 served from
  `Tools/lib/app_ui/vendor/` (`/vendor/<name>`, 1-year immutable cache, 302 to cdnjs only when the copy is missing), both scripts `defer`, MapLibre CSS non-blocking;
  `78dfbcd` ETag/304 + gzip for index.html, app.js, app.css, vendor (`H.send_body` / `H.send_static` in tesla_app.py, gzip kept per ETag, primed at start);
  `1b30eed` `boot()` asks for the settings together with the summary / status / keeper and only `route()` waits for them (step 3 complete);
  step 4: `c96b159` ETag/304 + gzip (level 1) for every 200 JSON answer to a GET (`send_json`, and the Kept lists with the stale flag in the ETag);
  then minute_kind no longer rewrites its file when the probe learned nothing (it caused an endless Drives-list rebuild loop), and the Drives list is kept on disk
  with its signature (+ code version) so a restart does not rebuild it (`app_drivelist.Cached`, file format `{kept_sig, list}`; an old plain file is shown stale as before).
  activity / chargers / places were already answered from the kept copy at once after a restart (app_kept.py) and rebuild fast (under 0.25 s), so they are left as they are.
  Then (next commit) `footage_root`: one look at the volumes for every role (`_look_roots`, was one `DR.discover()` per role) and a lock so the first request waits for
  the start-up thread's look instead of repeating it: first start's first answer 9.4-11 s → 1.5 s.
  `_warm` tried and left as it was (measured: starting it as soon as the window's first request is answered, in a value order and without /api/teslacam, made a
  second page opened 3 s after the first slower, 664 / 1,284 ms → 1,695 / 2,173 ms on a first start, because the warm-up competes with the page asked for; and
  /api/teslacam is cheap since the volume-look fix). Step 4 is done (`173ec36`).
  Step 5, next commit: `clip_clock.utc` kept per stamp (forgotten when the runs, the clips' own times or the settings are replaced): /api/clips warm median 0.235 → 0.203 s.
- **Next**: step 5 rest: clip_clock's provenance read incremental per file (`_skews_refresh` / `_read_skews`, keyed by mtime+size); ShortPlace re-fed only when
  its clip data changed (`app_teslacam._short_where`); /api/clips answered from what was built last and refreshed behind (the page's `tcGet` already shows its
  remembered copy at once; the server still builds on every call). Then
  step 5 (TeslaCam from cache), step 2 (perf_log), step 6, step 7. Order follows the task's Budget section (value first).
- **Half-done**: nothing.
- **Leads from the baseline**: (fixed: the ~10 s first answer was three drive discoveries per look, run again by the first request) `/api/teslacam` takes 1.7-11.8 s on every call, also warm (`_ROOTS["t"] = 0` then `TC.drives()` → `DR.discover()`); `/api/clips` 0.75-1.8 s warm; the first `/api/drivelist` after a first start 5.4 s.
- **Sandbox notes**: cdnjs is blocked here; the vendored files are `dist/` of the npm package `maplibre-gl@4.7.1` (the same files cdnjs serves). Playwright's headless shell is missing here; perf_run.py falls back to `/opt/pw-browsers/chromium` (or `PERF_CHROMIUM`).
- **Pre-existing test failures** (before any change here, also alone): `test_snapshots_for.SnapshotsFor.test_only_drives_over_the_clips_without_snapshots`, `test_timeline_stored.SentryWindow.test_the_minutes_of_a_sentry_event_make_a_window_with_telemetry`. The bar for each commit is: these two and no others.

## Measured so far (fixture, Linux sandbox; ms in headless Chromium, new page, empty cache)

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
