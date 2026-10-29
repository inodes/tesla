# Drive analysis screen (design, 2026-10-29)

Glenn: an analysis screen like the old Dashcam flip review (dashcam_flip_review_template.html: a card per event with the speed chart and a still a second, filters, calls), but for ALL detected events, and on the Drive page itself (reached from both TeslaCam and Drives (new)); not in Analytics or Alerts. It covers any drive that has anything detected (speed signs, school zones, FSD changes, accelerator, speed changes); every drive can be analysed.

## What it shows
- Tiles: events by kind, signs by category, signs that look wrong.
- Filters: kind, speed from/to (100 to 110, give or take), sign category (speed, school zone, roadworks, other), only possible errors, day.
- Table of events and signs in time order; the selected row opens the detail (the Review viewer: stills, chart lanes, big picture).
- Signs: the picture of each sign when detected (the crop kept in drive_review/crops), next to the car speed, the map limit (OSM), and flags: sign differs from the car, sign differs from the map, temporary (roadworks), exit or side road. The point is to find where the detection is wrong.
- Map of all events and signs. A sign is drawn where it appears to be, not where it was detected: the car position at detection plus the distance ahead, along the heading, offset to the side it stands on.
- Charts: speed before against after for speed changes, events per speed pair, signs read per category.

## Data
- Events: find_events() works from the dashcam telemetry alone (no video), so the list, chart and map can be built for any drive without the manual Run review; Run review stays for the stills and signs around each event.
- Signs: drive_review/<day>.json (every sign read in a whole-drive Reading signs pass: s, kind, text, x, y, r, side, crop) plus the signs in each event.

## Sign distance ahead (to decide / calibrate)
Pinhole: distance = f * D / (2 r), r = the ring's radius in pixels (sign.r), D = real sign diameter (speed signs are 0.6 to 0.75 m), f = front camera focal length in pixels. D and f are not known exactly; f*D can be fitted per drive from the signs seen in several frames: as the car closes on a sign, r grows as 1/(d0 - v t) with v from telemetry, so f*D follows from the track. Sideways offset from the ring's x position and the camera's field of view. Config keys under review.* for f and D until fitted.

## Steps
1. Rename the Review tab Analysis; list/chart/map built from telemetry events at once, Run review only adds stills and signs.
2. Table, tiles and filters across events and signs.
3. Sign gallery with the flags above.
4. Signs on the map ahead of the car (f*D fit, tests against a known sign).
5. Charts.
