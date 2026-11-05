# Load speed: notes and numbers

The task is docs/LOAD_SPEED_TASK.md. Work happens on branch `claude/load-speed` (cut from `merged`), pushed after every commit, with a small PR per step into `merged`.

## Handoff

- **Done** (all on PR #17): step 1 `25e635e` fixture + harness + baseline, `0aec053` harness page timings in Chromium; step 3 `adafa01` MapLibre 4.7.1 served from
  `Tools/lib/app_ui/vendor/` (`/vendor/<name>`, 1-year immutable cache, 302 to cdnjs only when the copy is missing), both scripts `defer`, MapLibre CSS non-blocking;
  `78dfbcd` ETag/304 + gzip for index.html, app.js, app.css, vendor (`H.send_body` / `H.send_static` in tesla_app.py, gzip kept per ETag, primed at start);
  next commit: `boot()` asks for the settings together with the summary / status / keeper and only `route()` waits for them. Step 3 is complete with it.
- **Next**: step 4 (gzip + ETag/304 for big JSON, lists kept on disk by inputs, smarter `_warm`),
  step 5 (TeslaCam from cache), step 2 (perf_log), step 6, step 7. Order follows the task's Budget section (value first).
- **Half-done**: nothing.
- **Leads from the baseline**: a first start answers its first request only after ~10 s although the port is bound at 0.24 s (something holds the request threads at start: look at what `/api/status` waits for, and the drivelist build thread); `/api/teslacam` takes 1.7-11.8 s on every call, also warm (`_ROOTS["t"] = 0` then `TC.drives()` → `DR.discover()`); `/api/clips` 0.75-1.8 s warm; the first `/api/drivelist` after a first start 5.4 s.
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
