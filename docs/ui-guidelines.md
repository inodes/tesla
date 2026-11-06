# UI guidelines for the app (Tesellate)

Working rules for every page, label and control in the app. Distilled from Nielsen Norman Group's ten usability heuristics, Apple's Human Interface Guidelines
("Designing for macOS") and design tips, and the UI articles by Fullstory and Upslide Design Studio. Paraphrased; the app-specific notes mark where a rule
decides something here.

## 1. Text on screen: functional, not narrative

- State what the thing is or does. No explanations of how it works, no stories, no apologies. If it needs a paragraph, it belongs in help, not on the page.
- Design the control so it needs no sentence. If a line is needed to say how to use it ("Pick one, two or all three", "A drive is only read when you pick it", "Use From and To on the rows below"), change the control until it is obvious (a toggle that shows on/off, a button that names its action, a disabled button with the reason as its label) and delete the line. A column or button label is one plain word the user already uses (Archived, Deleted), never a state name of ours ("Gone").
- Plain words the user already uses (drive, charge, parked, Sync from Tessie). No internal names (script names, file names, "stale", "ledger") in the UI.
- Labels for actions say the exact action ("Run review", "Sync from Tessie"), not "Go" or "Submit".
- One short line under a page title at most: source, scope, legend. Example: `Source: Tessie + dashcam · 🎬 = dashcam video and route`.
- Errors: say what failed and what to do next, in plain language, next to where it happened. No codes alone.
- Do not write "run ./script" or any command in the UI: the app does the work.

## 2. Status and feedback (NN heuristic 1, Fullstory, Upslide)

- Every action that takes more than about a second shows progress: what it is doing, and "N of M" or a percentage when the total is known. An indeterminate bar only when it is not.
- The bar moves smoothly: report progress inside long steps, not once per big item. A bar that sits still looks hung.
- Show the result when it ends (counts, what changed) and clear the "needs doing" prompt that caused it. A banner must not reappear after the action it asked for.
- Content that is still loading shows a neutral placeholder, never black or empty tiles.
- Every region (tiles, tables, filter rows, thumbnails, videos, maps) is on screen from the first paint as a placeholder block of its final size (class `ph`, height in `--ph`), and the text, picture or video replaces it in place. Nothing below it moves when the data arrives. What was shown last time is drawn first (remembered in the browser) and refreshed behind it.
- Slow pages show what they have first (stale-while-revalidate) and say that it is refreshing.

## 3. Control and error prevention (NN 3, 5, 9)

- Long jobs can be stopped (Stop button); stopping leaves a valid state.
- Destructive actions ask first, name what will be affected, and prefer move-to-folder / undo over delete.
- Optional features appear only when they apply. If a control has nothing to do for this item, do not show it (no greyed-out clutter). Example: the TeslaCam tickbox exists only when a Tessie page and dashcam telemetry both exist.
- Defaults are the safe, common choice; remembered choices (filters, wide table, tickbox) persist per user.
- Prevent the error where possible (disable a run button when its inputs are missing, and say what is missing).

## 4. Consistency and standards (NN 4, Fullstory, Upslide, Apple)

- Same thing, same name, same look, everywhere: one word for each concept across Drives, Charges, Parked, TeslaCam.
- Colours and icons carry one meaning each (FSD / Autosteer / TACC / Manual colours, the P for parked, ⚡ for charging). Legends sit under the map.
- Use platform controls and behaviour: native-feeling buttons, checkboxes, menus, keyboard shortcuts. Do not invent a new control for a standard job.
- One source of truth per fact. A parked place is one row, not three; an ongoing state (parked now) has no end time until it ends.

## 5. Recognition over recall (NN 6)

- Show options and state on screen: current filter, current day, which source a number came from (Tessie or TeslaCam).
- Keep controls next to what they change: a map option under the map, a table option in the table toolbar.
- Selected / active state is always visible (chips, rows, tabs).

## 6. Efficiency for repeat use (NN 7, Apple)

- Keyboard shortcuts for frequent actions; menu bar commands for everything the app can do.
- Remember last day, filters, sort, column mode. Fewer clicks to the common task: open the latest day with its map.
- Dense tables for scanning: wide mode with the numbers (distance, energy, efficiency, battery, self-driving, top speed) in columns, sortable, one row per event.

## 7. Layout, hierarchy, density (Fullstory, Upslide, Apple macOS)

- Mac screens are large: show more at once with fewer nested levels and fewer modal dialogs, but keep a comfortable density.
- Let the user resize panes and tables; layouts adapt to window size; full screen works.
- Hierarchy by size, weight, colour and whitespace: the main thing first (the map, the drive list), secondary details smaller and muted.
- Align text, numbers and controls on a grid; right-align numbers; units in the column header, not repeated in every cell.
- Whitespace groups related items; do not hide content behind accordions that make the user guess where it is.
- Primary action looks primary (one per area); secondary actions are quiet.

## 8. Legibility and accessibility (Apple tips, Fullstory, Upslide)

- Strong contrast between text and background in light and dark mode; never colour alone to carry meaning (add an icon, label or pattern).
- Minimum text size 11 pt (Apple's tip), most body text larger; line height that does not let lines touch.
- Click targets at least 44 x 44 pt on touch devices (Apple's tip); on Mac keep targets comfortable for a pointer and never tiny.
- Follow the system appearance (light / dark) and system font; support the system accent.
- Images and maps at their intended aspect ratio; sharp on Retina (2x / 3x assets).
- Test with keyboard-only use and with a screen reader.

## 9. Mac behaviour (Apple HIG: Designing for macOS)

- Windows can be resized, moved, hidden, shown and put full screen; remember size and position.
- The menu bar holds every command, with shortcuts shown; context menus on rows and the map.
- Support precise pointer work (hover details, tooltips, click a point on a chart to jump the video).
- Support personalisation where it pays: units, time zone, colours, which columns, toolbar contents.
- Switching apps must be smooth: inactive windows look calm, work continues in the background, nothing restarts on return.

## 10. Process (NN 10, Fullstory, Upslide)

- Build around a user goal and its task flow: find a drive, see where and how it went, see the footage, fix a gap. Judge a change by whether that flow got shorter.
- Prototype small, try it on real data (this user's own drives), and measure the real load time before and after a change.
- Watch for frustration: dead clicks, repeated clicks, waiting with no feedback, a control that does nothing.
- Keep a short list of reusable patterns (list + map, filter chips, progress bar, empty state, error box) and reuse them instead of one-off designs.
- The back link at the top of a page (`crumbs()` in app.js) returns to the page the user came from, never to a fixed parent. The path is kept per tab in sessionStorage; the page passes only a fallback for when it was opened directly. A main-menu click starts a new path, and saving or deleting a place replaces the page it was done on (`NAVREPL`).
- Help is short, findable and shown where needed (a tooltip on the control, a one-line hint), not a manual on the page.

## 11. One look between windows (for every agent that changes the app)

The same thing looks, reads and behaves the same on every page. Before writing markup for a new window, find the piece that already does the job (names are in `Tools/lib/app_ui/app.js`, styles in `app.css`), call it, and change it there if it must change: a fix then lands on every window at once. A copy pasted into a second page is a defect (two windows once opened one Sentry event two ways: one page, one code path).

**Shared pieces, and where each is used**

| Job | Use | Not |
|---|---|---|
| Date bar of a list (Drives, Charges, Parked, TeslaCam) | `rangeBar()`: `‹ day or range ▾ ›`, then the order buttons | a bar of your own |
| Order of days and of times in a day | `ORD` (`dayDesc`, `rowDesc`, remembered, one setting for all windows), buttons from `ordBtns()`: calendar + arrow for days, clock + arrow for times, arrow down = newest first; sort flat tables with `ordCmp` (the Date column of `table()` and of Charges already does) | a "Sort" label, a Days / Times word button, a per-page order |
| Flat sortable table | `table()` | a hand-made `<table>` with its own header sort |
| Tiles above a list | `tile(value, caption)` in `.tiles` | |
| Clip / event / sign cards | `.evs` + `.evc`, picture first, time bold, one muted line; pictures from `sentryThumb()` / `lazyThumbs()` | a card with another width, ratio or order |
| One drive, park or recording page | `teslaDriveBody()` (map, chart, video, cards), the video from `clipPlayer()`; a Sentry or Saved event opens the page of its drive or park with the event picked (`rowHashAt(u, ev)`) | a second window for an event |
| Where a thing is | `whereLink`, the registry's names for drives and places; the overlay on the video (`placeTime`, `evInfo`) shows the place the page shows | a place that is on the heading but missing on the video |
| Back link | `crumbs()` | a fixed parent link |
| Progress / errors / empty | `pbar` / `setPbar`, `errBox`, `.empty`, placeholders `.ph` | a spinner, a bare message |
| Time, number, length | `hms`, `clockHM`, `dayName`, `n0`, `dur` | `toLocaleString` or a new format in a page |

**Rules**

1. **Names come from data, never from the code.** Drive names (Archive, TeslaUSB, JOWUA, LaCie) come from the registry (`config.json` `teslacam.drives`, `drName` / `DRX` / `NMD` in the page, `lib/drive_roles.py` on the server); place names from the places data. No drive, place, road or person is typed in a string, a label, a test or a default. Wording that depends on a name is built from the name, not written beside it ("the Archive", not "Archive").
2. **One icon, one meaning, on every page.** Calendar = days, clock = time of day, arrow down = newest first / larger first, `‹ ›` = earlier / later, 🎬 = dashcam video, a red-ringed ? sign = questionable signs (crossed through: hidden). A control that has an icon has no word beside it; the word is the `title` and `aria-label`. Reuse the existing SVG (`calSvg`, `clkSvg`, `ARW`) before drawing another.
3. **A choice made in one window holds in the others** when it means the same: the order of days and times, the units, the time zone, the wide table. Keep it in one remembered setting (`lsGet` / `lsSet`), not one per page.
4. **Same place for the same control.** Date bar, then filters, then search, left to right, above the tiles; a map option under the map; a table option in the table toolbar; Stop beside the bar it stops. A control that does nothing for this item is not drawn.
5. **Late content never moves what is already shown.** A region that fills in after the first paint (event cards, signs, thumbnails, telemetry) has a fixed place in the layout below the main thing, or a placeholder of its final size. The video stays at the top while its cards load, and opening a page on an event does not scroll (`#tdb.side>#dev` and `#drv` have grid rows after the telemetry).
6. **A thing a window hides, every window hides.** If a sign, clip or row is set aside (overridden, off our road, a repeat, a place on the ignore list), it is left out of the list, the map, the badges beside the video and the count alike, and a single button shows the set-aside ones (`asideSign`, `SASIDE`): a red-ringed speed sign with a ? in it (`qSign`), crossed through while they are hidden, the cross off while they are shown. It is not listed with "(overridden)" beside it.
7. **Same data, same number.** A length, count, time or place shown in two windows comes from one function and one field. If two windows disagree, one of them is wrong: find the field, do not patch the text.
8. **Spacing and look** come from the tokens and classes in `app.css` (`--ink`, `--line`, `--card`, `--acc`, `.chip`, `.card`, `.bar`, `.sub`, `.pill`); no colour, font size or margin written into a page's markup unless the style is used once and says why. Light and dark both stay correct.
9. **Text** follows section 1 on every page: functional, plain words, one short line under the title, no command names, no explanation of how a control works.

**When you add or change a window**

1. Open two existing windows next to it (for a list: Drives and Charges) and match their bar, tiles, table / cards, empty state and loading state.
2. Search `app.js` for the job (`rangeBar`, `table(`, `.evc`, `teslaDriveBody`) and call what is there. If it nearly fits, extend it with an option and update every window that calls it.
3. If it needs a new piece, put it in the shared section, name it for the job (not the page), use it in at least two windows or say in a comment why one, and add a row to the table above.
4. Add a text test that pins the shared call (for example `tests/test_teslacam_filters_ui.py` `test_sort_arrows`, `tests/test_event_cards_below_video_ui.py`) so a later change cannot give one window its own copy.
5. Look at the result in the running app on real data, in the windows that share the piece, not only the one you changed.

## Checklist before a page or control ships

1. Is every sentence functional and short?
2. Does anything that waits show progress, and clear itself when done?
3. Does every optional control appear only when it applies?
4. Is each fact shown once, with its source clear?
5. Does it work in light and dark, resized, and with the keyboard?
6. Is the common task fewer clicks than before?
7. Does it reuse the shared piece from section 11 (date bar, order buttons, table, cards, drive page) and look the same as the other windows?
8. Does it hide what another window hides, show the same numbers and names, and keep names out of the code?
