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

## Checklist before a page or control ships

1. Is every sentence functional and short?
2. Does anything that waits show progress, and clear itself when done?
3. Does every optional control appear only when it applies?
4. Is each fact shown once, with its source clear?
5. Does it work in light and dark, resized, and with the keyboard?
6. Is the common task fewer clicks than before?
