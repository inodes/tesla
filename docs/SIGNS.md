# Signs: what is read, what is kept, and every number that can be tuned

This file is the single place for how speed-limit and school-zone signs are found, checked and trusted, so that none of it depends on a conversation. Change a rule in the code and change it here.

## 1. Finding a round limit sign (`lib/sign_finder.find_signs`)

1. Red-ring candidates: pixels whose Lab a* is well above the frame's median, contours with a round bounding box (aspect 0.78-1.28), ring-like fill (0.15-0.72), inside not solid red (< 30 % red inside 0.58 r).
2. The face (0.74 r) is read by `sign_reader.read` (a 32 x 32 MLP, classes 10..130 + none). Its confidence is over-sure on things it was not trained on, so the reading alone is never enough.
3. **Ring gate** (`sign_finder.ring_check`): the candidate is kept only when
   - `cover` >= `review.ring_cover` (default **0.80**): the share of 48 directions round the circle (radius searched 0.8-1.15 r, ring sampled at 0.78-0.96 of the radius) that have red (a* >= 9 above the area's median), and
   - `redin` <= `review.ring_redin` (default **0.30**): the share of the face (inside 0.5 r) that is red. A tail light or a stop sign is red inside.
   - `white` (bright share of the face) is reported but is NOT part of the gate: the dashcam's green cast makes real faces grey (L 85-140), and an absolute brightness limit threw real signs away.
   Result on the reference drive (Sun 8 Nov 2026, 10:56-11:12, Home to Top Ryde): the one real 70 passes; the car bumper "130", the tree/blur "40", "50", "60", "70", "110" and the roundabout give-way board "100" are all dropped. On 86 stored detections from other days every rejected one was a non-limit (no-turn signs, a Toyota badge, car rears, trees); every real limit passed. Records carry `ring` (the cover) from now on.
   Lower `ring_cover` (0.7) to recover signs in the rain or at night; raise it to be stricter. Re-read minutes to apply a change.
4. A "40" on the back of a school bus is dropped (`vehicle_rear.on_vehicle`).
5. Other keys: `review.min_conf` 50 (reader confidence), `review.refine_conf` 85, `review.step_s` 1.0, `review.merge_s` 12, `review.min_kmh` 30 / `end_kmh` 10 / `end_s` 180 (the speed a minute must reach to be read).

## 2. Trust (`app.js`, HUD block: `hudTrust`, `hudSpike`, `hudJump`)

Trust of a reading = lateral position x car-speed answer x override, 0-1; under `HUD_DOUBT` (0.3) it is not held as the limit and the list says why.

| Why | Meaning | Where tuned |
|---|---|---|
| lat "off our road" | the sign stands too far from the car's line (full up to 5 m, a quarter at 14 m) | `hudWLat` |
| spd "car did not slow" | the car holds well below the reading | `hudWSpd` |
| ovr "overridden" | a stronger different sign was read within 15 s | `hudTrust` |
| spk "between lower signs" | a high reading between two much lower agreeing signs: ruled out (t = 0) | `HUD_SPIKE` = jump 30, near 10, span 90, slow 0.65, far 300, nearFar 20 |
| **jmp "sudden jump, not confirmed"** | a reading `jump` km/h or more from the latest earlier sign (within `span` s) that no other sign of that number confirms (a different sign: more than `same` s away, within `confirm` s) and the car's median speed (+-10 s) is not within `car` x the reading. Counts at `mult` of its trust and is flagged `q` (questioned). | `HUD_JUMP` = jump 40, span 120, confirm 60, same 4, car 0.85, mult 0.4 |

A jump that is real (a ramp onto a motorway) is confirmed by the car's speed or by the next 100/110, so it stays. A lone jump that nothing confirms is a likely misread: it shows as questioned and is held back when it was weak anyway.

## 3. School zones (`lib/sign_classes.find_school`, `lib/school_days.py`, HUD `hudSchool`)

- The board is a fluorescent yellow-green rectangle reading SCHOOL / ZONE (fuzzy), with at least `SZ_MIN_BLOBS` (3) letter-sized dark shapes; the plate under it carries the hours; the limit is the red-ringed 40 beside it.
- **Hours snap** (`snap_times`): the NSW plate is "8-9:30 / 2:30-4"; the small raised 30 is read as 00 or lost, giving 08:00-09:00 / 14:00-16:00. A reading whose every range starts within `SNAP_START` (30 min) and ends within `SNAP_END` (30 min) of a standard slot (`STD_SCHOOL_HOURS` = 08:00-09:30, 14:30-16:00) is given as the standard hours and the record gets `tsn: true`. Anything else stays as read; an unreadable plate gives no hours (never guessed).
- School days: `resources/school_terms.json` has the student term dates of **every state and territory for 2026 and 2027** (NSW, VIC, QLD, ACT, SA, WA, TAS, NT; sources in the file), plus each state's statewide public holidays that fall inside a term (`holidays`; NSW uses `agl_common`). The state comes from where the car was (`au_state` via `/api/schoolday?date=&lat=&lon=`). An unknown state or year is "unknown", never a guess. Local show days, pupil-free days and a school's own closures are not listed: add them to the file. State on the HUD: on (school day, inside hours), off, unknown (no hours or no calendar). Add 2028 when the states publish it.
- On the sign cards of a school zone (the board and the limit within 8 s of it) the page writes the plate's hours (or, when the plate cannot be read, the NSW standard 08:00-09:30 / 14:30-16:00 marked "standard hours assumed"; other states: "hours not readable"), whether that day is a school day where the car was, and whether the hours cover the time of the sign.
- Known weakness: stored school records from 28-30 Oct 2026 include green blur / grass read as boards (no hours). The current detector rejects most of them; re-reading those minutes clears the rest. A plate-brightness check was tried and removed: it did not separate false boards from real ones and lost real ones.
- Open: a 40 ring read with no board near it is a normal 40 (not a zone); a board with no readable 40 within 8 s is shown as a zone with the limit unknown.

## 4. Checking against the map and making training data (`Tools/maintenance/sign_check_drive.py`)

Run on the Mac (Overpass is not reachable from the cloud workspace):

    ./Tools/maintenance/sign_check_drive.py --day <YYYY-MM-DD> --from <HH:MM> --to <HH:MM>

It matches each kept sign picture to where the car was, the nearest OSM way within `--max-m` (25 m) that has a `maxspeed` tag (all road classes; one Overpass query kept in `<data>/roads/maxspeed_<day>.json`), and writes `<data>/drive_review/signcheck/<day>_<from>.csv` (read, map limit, road, car km/h, verdict agree/near/differ/nomap, ring cover). Most suburban streets carry no `maxspeed` in OSM (`nomap` says nothing).

Training faces go to `<data>/sign_training/real_faces.npz` (g 32x32 grey, t label, d day, c 100):

- positive: agrees with the map and passes the ring gate, labelled with its number;
- hard negative ("none"): differs from the map by 30 km/h or more and fails the ring gate;
- the person's own answer in `<data>/sign_training/overrides.json` (`{"<stamp>|<second>|<text>": "60" | "none"}`) wins over both;
- everything else is left out.

Retrain on any machine with numpy, opencv, pillow, scikit-learn: `python3 Tools/maintenance/train_sign_reader.py --out Tools/lib/sign_reader_model.npz --real <data>/sign_training/real_faces.npz` (real faces are repeated 4x; days with day-index % 4 == 0 are held out and scored).

## 5. Answers on the sign cards (`lib/sign_feedback.py`)

On the drive page, "Signs read in this video" can be collapsed from its heading (the Hide / Normal chips as before). Each sign card has a small box in its corner (✕): it opens a reason dropdown.

| Reason | What it does |
|---|---|
| Wrong speed (then the real speed) | the card, the map pin, the speeding check and the limit shown use the speed given |
| Right speed, it is a school zone / not a school zone | sets `zone` on the sign: the card says whether it is a school zone's limit, with hours and school day |
| Not a speed sign (a red rear light, a bumper…) | the sign leaves the list, the map and the checks; it is kept under "N removed as not speed signs" with Put back |

An answered card shows ✎ and what was answered; Undo my answer (or Put back) removes it. Answers are in `<data>/sign_training/feedback.json` (`stamp|second|number as read` -> reason, speed, seconds the sign spans); the ones that say what the sign really is are also written to `overrides.json`, and `sign_check_drive.py` labels every still of that sign from them ("none" = hard negative, a number = positive), so the next retrain learns from them. The video overlay picks an answer up the next time the drive page is opened.

Pending: per-session "re-check from the session page"; tracking one sign across frames in the video overlay (the box flickers between detections); pillar cameras as a second sign source; other sign classes.
