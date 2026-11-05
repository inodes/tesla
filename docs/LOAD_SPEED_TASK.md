# Task: make Tesellate load faster

You are working on Tesellate, a Mac app (Python server + pywebview window + a single-page UI) that combines Tessie drive/charge data with TeslaCam dashcam footage. Code: Tools/tesla_app.py (server), Tools/lib/*.py (app_*.py services, teslacam_timeline.py, clip_clock.py, sign_finder.py ...), lib/app_ui/index.html, app.js and app.css (UI, app.js is ~460 KB; all under Tools/lib/app_ui), tests/ (unittest), docs/COMMANDS_AUDIT.md (a dated line per change).

GOAL: make the app load faster, without changing what it shows or does. Commit on the working branch you are given; do not touch main or any remote beyond what the session itself does.

## What is known (from a timing run on the real data folder, warm file cache, Linux sandbox)

- Server start to first answered request: about 2 s (5 s once). On the Mac the launcher is `zsh -lc 'cd Tools && exec python3 tesla_app.py'`, so a slow login shell may add more; that cannot be measured from here.
- Endpoint times and sizes, cold process: /api/activity 0.1-0.4 s, 1.1 MB; /api/drivelist 0.6-1.0 s, 2.2 MB; /api/chargers 0.1-0.24 s, 1.7 MB; /api/alerts 0.06 s, 0.65 MB; places, battery, analytics, summary under 0.1 s.
- /api/teslacam (TC.drives(), called with `_ROOTS["t"]=0` which forces a rescan every call) took 39 s in the sandbox because it walks the connected TeslaCam drives (large USB disks: files 54k, 23k, 13k, 2k). /api/clips (TC.clips_all: all_minutes, _events_kept, provenance reads) did not finish in 190 s there. These run on every TeslaCam page open.
- Lib/clip_clock.py `_skews_refresh` re-reads every teslacam_provenance/*.json whenever any file's mtime changes (checked every 20 s); the background keeper writes those files continuously while it reads a drive, so a request thread pays a full re-read repeatedly. app_teslacam `_short_where` re-feeds a ShortPlace index when CC.version() changes.
- Lib/app_ui/index.html loads the MapLibre CSS and JS from cdnjs in <head> (render-blocking, needs network) although only map pages need it.
- app.js `boot()` and `route()` await several fetches in sequence; /api/* payloads are not compressed or ETag'd; `_warm` fetches 11 endpoints 8 s after start.
- A TeslaCam page recently showed "Reading the drive…" for JOWUA and then "Load failed" (a dropped request); cause not yet found. The server now writes a thread stack dump to ~/Library/Logs/tesla_app.log when a request runs over 20 s.

## Steps (in this order; each its own commit with a dated line in docs/COMMANDS_AUDIT.md)

1. Measurement first. Write make_perf_fixture.py (in the Tools/maintenance folder) that generates a realistic synthetic data folder (several months of drives, charges, Tessie CSVs/JSON, teslacam_provenance/ledger/timeline files, and a fake TeslaCam drive tree with tens of thousands of tiny files) and a harness perf_run.py (same folder) that starts the server against it and times start-up and every /api endpoint cold and warm. Record the baseline in docs/PERF_NOTES.md.
2. Real-world logging, so the owner can get true numbers: lib/perf_log.py appending to ~/Library/Logs/tesla_perf.log (rotate at 1 MB): start-up marks (time since launch via `ps -o etime= -p <pid>`, setup done, server bound, first request), every /api request over 0.15 s with its bytes, and client timings sent by app.js to a new POST /api/perf (route start to page rendered, and first-page-after-launch time from performance.now()).
3. Start-up: vendor MapLibre 4.7.1 (JS+CSS from cdnjs) into lib/app_ui/vendor/ and serve it locally with long-cache headers (fall back to the CDN URL only if the local file is missing); nothing in <head> may block the first paint; parallelise boot()'s fetches; serve app.js/app.css with ETag/Cache-Control and gzip.
4. Server: gzip + ETag/304 for the big JSON endpoints; keep the built lists (drivelist, activity, chargers) on disk keyed by their inputs' mtimes so a restart does not rebuild them; make `_warm` smarter (only what the first page needs first).
5. TeslaCam page: /api/teslacam and /api/clips must answer from cached state immediately and refresh behind (the owner's rule: "refreshed behind, never waited for"); no full drive walk on page open; the walk happens in the background keeper only. Make clip_clock's provenance read incremental (per file, keyed by mtime+size) instead of re-reading all files; do not re-feed ShortPlace unless the clip data it uses changed.
6. Drive page (route #/teslacam/auto/win-...): profile pageTeslaCamAt / teslaDriveBody and the endpoints they call on the fixture; fix the biggest costs.
7. Re-run perf_run.py, put a before/after table in docs/PERF_NOTES.md, and list what needs checking on the real Mac (read ~/Library/Logs/tesla_perf.log after a normal day's use).

## Rules

- Behaviour must not change. Run the whole unittest suite before each commit (python3 -m unittest discover -s tests); add tests for any new cache or incremental logic, including that a changed input invalidates it.
- Every performance claim in a commit or in PERF_NOTES.md needs a measured before and after number from the harness.
- UI text: no explanatory or instructional sentences in the UI (the owner has a firm rule against over-explaining); labels only.
- TeslaCam first: footage and its own GPS/provenance are the primary data, Tessie only fills what the footage lacks; the six cameras of a minute stay together, never cut mid-file.
- Never read the owner's real data, call Tessie or any API with credentials, or write outside the repo. Network use is limited to downloading the pinned MapLibre 4.7.1 files from cdnjs and normal package installs.
- Commit messages end with:
  Co-Authored-By: Claude <noreply@anthropic.com>
- Finish with a short summary: what got faster by how much (measured), what is unmeasured because it only shows on the real Mac, and anything you chose not to do.
