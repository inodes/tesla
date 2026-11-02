# Native macOS app (step 2): SwiftUI front, Python engine

Status: TODO-072, a serious TODO, not started. Step 1 (polish the current shell) is done first and is described at the end.

## Why
Glenn wants the app as native to macOS as possible. Today Tesellate is a Python server (Tools/tesla_app.py) shown in a pywebview window (Apple's WebKit) from a launcher .app (Tools/build_mac_app.sh). The weak spots of that shape are playback of the six dashcam cameras (browser video, hand-written sync), the look and feel (no real menu bar, Dock, Quick Look, drag and drop), and needing Python on the PATH.

## Shape
- A Swift app (SwiftUI, AppKit where SwiftUI is not enough) owns the windows, lists, charts, maps and video.
- The Python code stays as the engine: Tessie, ffmpeg, sign reading, telemetry, the keep / delete jobs, the footage log. The Swift app starts it as a child process (bundled Python or the repo's venv), talks to it over the existing local HTTP API (127.0.0.1, a random port and a token), and stops it on quit.
- Screens move over one at a time; the web UI keeps working until the last one has moved (the Swift app can show a web view for a screen not moved yet).
- Nothing in the Python engine becomes Swift: the tests stay where they are.

## Order of work
1. Shell: Swift app that launches and supervises the engine, a window with a web view of the current UI (replaces pywebview and the launcher script), menu bar, Dock, notifications.
2. Video player: AVPlayer for each camera, one master clock, speeds 0.5x to 16x, frame stepping, the six cameras in one view; the clip list with thumbnails from AVAssetImageGenerator.
3. Lists: TeslaCam (Recent / Saved / Sentry cards with the place filter and date bar), Drives, Parked, Charges.
4. Drive page: telemetry charts (Swift Charts), the map (MapKit or MapLibre Native), the analysis screen (docs/DRIVE_ANALYSIS_SCREEN.md).
5. Keep / delete jobs, Tasks and sync progress as native progress and notifications.
6. Quick Look and drag and drop of clips, Shortcuts / App Intents if wanted.

## Working constraints (how it gets built)
- Claude's shell on the Mac is a Linux VM: it cannot run Xcode, xcodebuild or any Mac GUI code. Claude writes the Swift files (SwiftPM or an Xcode project in the repo); Glenn builds on the Mac and sends back errors and screenshots. Computer use on the Mac screen is possible but slow.
- So the Swift stays thin: no logic in it that could live in the Python engine, where it can be tested here. Pure Swift logic (clock sync, date maths) goes in a SwiftPM package with unit tests Glenn runs with `swift test`.
- The Python HTTP API becomes the contract: list it, version it, test it, before screens move.

## Decisions still open
- Minimum macOS version; SwiftUI only or AppKit for the video grid.
- How the engine is shipped: the repo's venv (as now) or a bundled Python inside the .app.
- Maps: MapKit (native, no tiles of our own) or MapLibre Native (the same styles as the web maps).
- Distribution: for Glenn's Macs only (ad-hoc signed) or signed and notarized with a developer account.
- Whether the web UI is kept for the browser use (iPad / phone on the same network).

## Step 1 (done first): polish the shell, Python side
lib/mac_shell.py: a menu bar with the pages, Dock badge with the running job, a notification when a long job finishes, Cmd-1 to Cmd-9 for the pages. No Swift. The proper .app bundle with its own Python is the next part of step 1.
