# Architecture overhaul - design for discussion

Status: DRAFT for Glenn to mark up. Nothing here is built. Companion to `COMMANDS_AUDIT.md` (which it supersedes where they differ).

## 0. Scope: undo the mess, keep the logic

What exists works: Tessie drives are enriched with the video's per-second readings (`drive_fuse`), charges and footage are consolidated, the reports and pages are right. This work reorganises scripts, commands, files and libraries and removes duplicates. **It does not change what any calculation or enrichment does**, and reference results taken from today's outputs prove that. Anything that changes behaviour is a separate, named feature agreed on its own - for example, drives built from video alone for someone without Tessie (section 3) is new and additive; it must not alter how Glenn's Tessie-plus-video drives come out.


1. **Tesla files are the core; every third party is optional.** Anyone using this has Tesla video files. Tessie, AGL, Evnex, PVOutput, SolarEdge, ABRP, fuel prices and the charger finders are each an optional addition. Nothing in the core may import a provider.
2. **The app is the main surface.** It calls libraries through one task runner. The command line is a thin second surface over the same tasks. Neither holds logic.
3. **Logic lives in libraries, once.** One owner for each idea (clock, places, drive detection, cost model...). No twin scripts, no logic that only exists inside a command.
4. **Work is stored and not repeated.** Every processing step records what it has done and at which version; a later run skips it unless forced.
5. **Nothing is deleted until its replacement reproduces today's results** (reference results, section 8).
6. **A new top-level command needs agreement.** New work goes into a library and an existing task.
7. **Store separately, consolidate in the view.** Each source's data is stored on its own and never changed by another source. Consolidation happens when something is viewed (a drive page, a report, a map), not in the stored data. Any cache of a consolidated result is disposable and rebuilt from the sources. Unmatched records show as unmatched, and the source of every value stays visible.

## 2. The line between core and providers

| Core (Tesla files, always present) | Optional providers (each off until configured) |
|---|---|
| TeslaCam drives and their registry (by volume UUID; role car / archive / copy) | **Tessie**: drive energy, battery, odometer, temperatures, addresses, charges, alerts, idles, tyre pressure, battery health |
| Video telemetry: speed, gear, pedals, steering, blinkers, FSD/Autosteer/TACC, GPS, heading (about 36 readings a second) | **Evnex**: home charger sessions |
| Drives built from that telemetry | **AGL**: tariffs, usage, demand |
| Footage log (what is on which drive, deleted or not) | **PVOutput / SolarEdge**: solar |
| Stills, notable moments (FSD hand-back, accelerator, speed changes, saved clips), signs, traffic | **ABRP**, **fuel prices**, **charger finders** (OSM, PlugShare, Tesla) |
| Places derived from where the car stops (OpenStreetMap names optional) | Cost, ROI and efficiency reports that need charges and tariffs |
| The "safe to delete" rules and freeing space | |

Charging and cost have no core source: the video carries no battery or charge data. Without a provider those features do not appear; the app shows what the files can give.

## 3. The drive record: a view over whatever sources are present

There are at least one and up to two stored sources of drives: the video telemetry store (per-second readings of what was recorded) and the Tessie master (`drives_master.csv` and the per-drive samples; it is Tessie's own consolidated store, not a master of all sources). Neither is rewritten from the other. A drive as shown is a view over whichever are present. For Glenn that is both; for most people it will be video only.

| Field | From video | From Tessie | Notes |
|---|---|---|---|
| Start / end time | car clock (see 5) | UTC | |
| Start / end position | GPS | GPS | |
| Distance, average and maximum speed, duration | integrated from speed | reported | |
| Autopilot / FSD distance | from autopilot state | reported | |
| Odometer, battery, energy, range used, temperatures | - | yes | provider only |
| Address, saved place | OSM lookup (optional) | yes | |
| Footage coverage, stills, events | yes | - | always core |

Rule (it depends on what is asked, and matches how the app already combines them): video is more frequent and accurate, so wherever the footage covers an instant its per-second readings are used; Tessie is the more complete timeline, so it supplies the drive's boundaries, the stretches the video did not record, and everything the video cannot know (energy, battery, odometer). Every shown value remembers which source it came from. A drive that exists only in Tessie (history from before the footage began) stays a provider-only record; a column no source supplies is simply empty, and every consumer must cope with that.

## 3a. Overlapping data: observations, not overwrites

Two sources describing the same thing rarely describe it the same way. Merging them into one row hides that, double counts, and breaks when one side is partial. So each source keeps its own records untouched, and the view links them into the thing the user sees, with a match rule and a confidence, each time it is asked for.

**Drives.** (Today's `drive_fuse` already does this at view time: Tessie's own points with the dashcam's per-second readings where they exist.) Tessie records whole drives, start to finish, by its own detection. The video records only what the car recorded: it may begin or end mid-drive (recording started late, a clip missing, the USB full, Recent clips overwritten), and have gaps. A video "drive" is therefore a **recording segment**, not a drive.
- With Tessie: the drive is Tessie's; video segments attach to it as coverage (footage %, first and last covered second). One Tessie drive can have several segments; one segment can span two Tessie drives (a short re-park between them).
- Video only: drives are inferred from segments and gaps and **labelled partial** (may begin or end mid-drive, with the covered span shown). Their distance and times are lower bounds, never treated as a whole drive's totals, and not summed with complete drives without saying so.

**Charging.** The same plug-in is recorded three different ways, and none is "the" truth:
- Tessie (car side): state of charge start and end, energy added to the battery, location.
- Evnex (charger side): energy delivered at the wall (more than the battery got, because of losses), session times from plug-in, not from charging.
- Tesla Supercharging: the billed energy, price, idle fees and GST.
Each is stored as an **observation of a charge event**; the view links observations that overlap in time and place into one event. Reports say which source answers which question: cost comes from the biller (Evnex/AGL/Tesla invoice), energy into the battery from the car, and the difference is the loss. An observation with no partner is shown as unmatched, never merged on a guess.

**Today's consolidation is the baseline, not the thing being replaced.** The way drives, charges and footage are consolidated now works, and Glenn confirms it. Section 3a names and protects that behaviour; it does not redesign it. The reference results (section 8) are taken from the current consolidation first, and the new views must reproduce them exactly before anything changes.

**Store facts, derive names.** A stored record holds what was measured: times (UTC), GPS, distances, readings. Place names are worked out from GPS and the current places list when shown, by one resolver, so renaming or adding a place corrects every past drive. Today `drive_summary.json` stores the names (`frm`, `to`) with no GPS, resolved once by a second copy of the place lookup in `drive_report.py`, and the app prefers that stored name over its own lookup. The fix is to store start/end GPS and the same-level facts only, and have the app and the drive pages call the one resolver in the places library. The same applies to every other stored name or label (and to `flips`, which is zero in every summary and is being renamed or dropped).

**Agreed (names).** No "name as detected at the time" field is stored: it would go stale and compete with the GPS. Tessie's place names are not trusted and are never used for naming a place, not even as a last resort: the name comes only from our own resolver, worked out from GPS: the places the user manages with `places_manager` (`places.json`, the trusted place store), known chargers, and Supercharger names. Order: a Supercharger where a charge took place uses Tesla's tesla.com name (always, even over a saved place); otherwise a saved place; otherwise it is just an address, from `places_manager`'s own OpenStreetMap reverse lookup, worked out when shown and never stored. Adding the spot as a place makes that the name. Tessie's text remains in the Tessie store as that provider's raw record only. A video-only drive gets its name from its GPS the same way. If "what was this place called then" is ever wanted, it is a dated history inside the places list, not a copy in every drive. The summary rewrite is done as part of the drives clean-up (step 3 of section 9), read-only against the old names as the reference.

**What this guards against.** Double counting (the same kWh from two sources), partial data presented as whole, one source silently overwriting another, and a match that is wrong with no way to see why. Each reconciled item keeps the list of observations behind it.

## 3b. Things, not sources: adaptive sync and known gaps

Commands are organised by **thing** (drives, charges, events, camera, places and chargers, power, fuel, battery), never by source. TeslaCam, Tessie, Evnex and AGL are sources that feed things; a source is not a thing.

Each `sync_<thing>` works with whatever exists at that moment:
1. **Discover** the sources available now: connected drives (found by structure and volume UUID), a Tessie token, an Evnex login, a folder of invoices. A source that is not there is skipped without complaint, never an error.
2. **Pull in** what is new from every available source, skipping what is already recorded as processed (unless forced).
3. **Record coverage.**
4. **Show the gaps.**

**Coverage record.** For each thing, per day (with counts, and down to the item where needed), each source is in one of these states:
- *have*: pulled in, with the processing version that did it (a changed version means redo).
- *none exists*: the source was checked and has nothing for that day (e.g. no footage was recorded; Tessie has no telemetry before it started).
- *not reachable*: the source is known to hold data we have not pulled (a drive that was not connected, an API that was down), with where it was last seen.
- *lost*: the source's data no longer exists (footage deleted after its metadata, stills and snapshots were kept). What was kept stays and is marked as kept.

**Gaps** are derived from that record and always split three ways: can be filled now (the source is connected, run the sync), needs a source (connect JOWUA, add the Evnex login), cannot be filled (lost, or never existed). The same record answers "is it safe to delete this footage?": only when its telemetry, stills and snapshots are all *have*. It also replaces the separate "processed record per day" idea, the file log and the skip checks, which are all the same bookkeeping.

The record is derived and kept in the data folder (one file per thing). It can be rebuilt from the stores, so it is never a source of truth.

**Who does what with camera files.** Two things touch them and they never overlap:
- **`sync_drives`** cares only about what the files *say*: it reads whatever camera files are reachable (any connected drive that holds footage, wherever it is) for their metadata, takes what drives need from them while they exist (the telemetry, the one-second stills of each clip, and the snapshots along each drive, all in one read of the clip), and combines that with Tessie if present. Stills and snapshots belong to drives. It does not copy, move or delete footage.
- **`sync_camera`** cares only about where footage is *kept*: which drive holds which files, copying to the right place, freeing space, and the registry of drives. It never opens a clip for its content.
They meet only through the coverage record: `sync_camera` deletes footage only when the record says the drives side already has what it needs from that footage.

**Filling in later.** A drive can be synced from Tessie alone and the camera files can arrive afterwards (a drive connected later, an old drive imported). Coverage then shows the camera source for that drive as a gap that can be filled when the source appears, and when it does, `sync_drives` fills in what was missing: telemetry, stills, snapshots, footage percentage, events. Everything that was derived from camera data (a drive page's footage figures, its snapshots) is marked out of date as soon as camera data for its days arrives, and redone; a drive that had nothing to show is not looked at again until then. The same holds for any thing and source pair: Tessie arriving after video, an invoice arriving after a charge.

**A thing exists only if some source knows it.** With Tessie, the drives (and charges) are known from the start, so a missing camera source is a visible gap that can be filled later. Without Tessie, a drive does not exist until camera files for it do: nothing says what was driven, so there is nothing to report as missing, and the only gaps visible are inside the footage itself (a break between clips, a drive that starts or ends part way, marked partial, see section 3a). Gap reports are therefore always relative to what at least one connected or previously seen source has recorded, and never claim a complete picture of what happened.

**Sources are configuration, never assumed.** Nothing is taken for granted: not Tessie, not a camera.
- A third-party provider (Tessie today; TeslaFi or another tomorrow) is a source only if it is set up in `config.json`. Not set up means it is not a source, and nothing mentions it or reports it missing.
- Tesla's own files (camera footage and its metadata) are a source whenever a drive holding them is found.
- **Backbone.** The first configured provider is the backbone: it says which drives and charges exist and gives the complete timeline, and the camera is an enrichment on top (video wins field by field where it covers, see section 3). With no provider configured the camera is the backbone, and a drive exists only where footage does. Which source is backbone is worked out from `config.json` every run, so adding a provider later changes it without any migration.
- A provider is a plug-in that says which things it can supply (drives, charges, idles, battery...) and how complete it is. That Tessie happens to be the only provider today is a fact about this installation, not about the design; the same slot takes TeslaFi or any other.
- Several providers at once are stored separately and combined in the view like any other overlap (section 3a). Their priority is the order they appear in `config.json` (open decision: confirm that is enough).

## 4. Layers

```
interfaces   app (tesla_app.py + UI)           CLI wrappers (about 20, one per task)
services     named tasks: id, title, options, run(progress, stop) - one runner, one Activity panel
domain       drives, footage, events, places, stills, delete rules | charging, costs, solar (need providers)
providers    tessie/ agl/ evnex/ pvoutput/ solaredge/ abrp/ ...   (each: what it can supply, configure, fetch)
data         storage and readers: files in the data folder, config, volume registry
```

Dependency rule: each layer may use only the layers below it; core domain never imports providers; providers only produce records the domain knows how to merge.

## 5. The clock

The car's clock is not UTC. Today it is learned by matching video GPS to Tessie drives. Without Tessie it falls back to the configured time zone (daylight saving applied), which can be an hour out for days after a clock change. Design: keep the learned clock when a provider exists; otherwise use the zone and mark times as "estimated". Everything is compared in UTC.

## 6. Packages (proposed)

`core/` (drives, footage, events, places, stills, registry, clock, delete rules), `providers/<name>/`, `services/` (tasks), `app/` (HTTP + UI), `cli/` (wrappers), `data/` (storage). The current `lib/` and the 60,000 lines inside the large scripts are redistributed into these; twins are diffed and removed; one-offs and tests go to `archive/` and `tests/`.

## 7. Storage

Follows Glenn's earlier direction: per-day JSON for derived metadata, CSV where Tessie provides CSV. Processing record per day: which steps (telemetry, stills, events, signs, traffic, snapshots) ran at which version; later runs skip them unless forced.

## 8. Safety net

Before any domain is restructured: capture reference results from the real data (drive and charge summaries, costs, drive pages, event lists) and require every step to reproduce them exactly. Today there are two test files for the whole code base.

## 9. Order of work (proposed)

1. Agree this document.
2. Reference results from today's outputs (read-only snapshots to compare against).
3. Clean-up with no logic change: group the existing code by domain, remove twins, move one-offs and tests out, one task per job, thin commands. Domain by domain, each step proving it reproduces the reference results. Charging and the remaining providers (AGL, Evnex, solar, fuel, chargers) follow the same way, one at a time.
4. Task runner and Activity panel in the app; CLI wrappers generated from the same task definitions.
5. Separate, additive features, each agreed on its own: processing record per day, drives from video alone for users without Tessie, further providers' reconcile views.
6. Retire every old script once its task passes the reference results.

## 10. Open decisions

- Whether the per-instant rule in section 3 (video where it covers, Tessie elsewhere) is the full rule, and the match rules and confidence for drives and charge events (section 3a).
- Naming: `drives_master.csv` is really the Tessie master, so it should live under the Tessie provider's own area and be named for it (and likewise AGL, Evnex, etc.), while the video stores sit under the core. Rename at the same time as the move, with the old path left readable until the app uses the new one.
- Package names and folder layout (section 6).
- Domain order after the core (section 9).
- Whether to run one cloud agent per domain once this is agreed.
