# Architecture overhaul - design for discussion

Status: DRAFT for Glenn to mark up. Nothing here is built. Companion to `COMMANDS_AUDIT.md` (which it supersedes where they differ).

## 1. Principles

1. **Tesla files are the core; every third party is optional.** Anyone using this has Tesla video files. Tessie, AGL, Evnex, PVOutput, SolarEdge, ABRP, fuel prices and the charger finders are each an optional addition. Nothing in the core may import a provider.
2. **The app is the main surface.** It calls libraries through one task runner. The command line is a thin second surface over the same tasks. Neither holds logic.
3. **Logic lives in libraries, once.** One owner for each idea (clock, places, drive detection, cost model...). No twin scripts, no logic that only exists inside a command.
4. **Work is stored and not repeated.** Every processing step records what it has done and at which version; a later run skips it unless forced.
5. **Nothing is deleted until its replacement reproduces today's results** (reference results, section 8).
6. **A new top-level command needs agreement.** New work goes into a library and an existing task.
7. **Sources overlap and disagree; never overwrite one with another.** Each source's records are kept as they came. What the app shows is a reconciled view built from them (section 3a), with the source visible and unmatched records shown as unmatched.

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

## 3. The drive record: built from whatever is present

`drives_master.csv` (the name stays for now) is built from at least one source: video telemetry, Tessie, or both. For Glenn it is both; for most people it will be video only.

| Field | From video | From Tessie | Notes |
|---|---|---|---|
| Start / end time | car clock (see 5) | UTC | |
| Start / end position | GPS | GPS | |
| Distance, average and maximum speed, duration | integrated from speed | reported | |
| Autopilot / FSD distance | from autopilot state | reported | |
| Odometer, battery, energy, range used, temperatures | - | yes | provider only |
| Address, saved place | OSM lookup (optional) | yes | |
| Footage coverage, stills, events | yes | - | always core |

Rules: every field remembers its source; where both have a value, a per-field rule decides (proposed: Tessie wins for distance, times and energy; video wins for anything second-by-second); a drive that exists only in Tessie (history from before the footage began) stays a provider-only record; a column no source supplies is simply empty, and every consumer must cope with that.

## 3a. Overlapping data: observations, not overwrites

Two sources describing the same thing rarely describe it the same way. Merging them into one row hides that, double counts, and breaks when one side is partial. So each source keeps its own records untouched, and a separate, re-runnable, versioned **reconcile** step links them into the thing the user sees, with a match rule and a confidence.

**Drives.** Tessie records whole drives, start to finish, by its own detection. The video records only what the car recorded: it may begin or end mid-drive (recording started late, a clip missing, the USB full, Recent clips overwritten), and have gaps. A video "drive" is therefore a **recording segment**, not a drive.
- With Tessie: the drive is Tessie's; video segments attach to it as coverage (footage %, first and last covered second). One Tessie drive can have several segments; one segment can span two Tessie drives (a short re-park between them).
- Video only: drives are inferred from segments and gaps and **labelled partial** (may begin or end mid-drive, with the covered span shown). Their distance and times are lower bounds, never treated as a whole drive's totals, and not summed with complete drives without saying so.

**Charging.** The same plug-in is recorded three different ways, and none is "the" truth:
- Tessie (car side): state of charge start and end, energy added to the battery, location.
- Evnex (charger side): energy delivered at the wall (more than the battery got, because of losses), session times from plug-in, not from charging.
- Tesla Supercharging: the billed energy, price, idle fees and GST.
Each is stored as an **observation of a charge event**; the reconcile step links observations that overlap in time and place into one event. Reports say which source answers which question: cost comes from the biller (Evnex/AGL/Tesla invoice), energy into the battery from the car, and the difference is the loss. An observation with no partner is shown as unmatched, never merged on a guess.

**Today's consolidation is the baseline, not the thing being replaced.** The way drives, charges and footage are consolidated now works, and Glenn confirms it. Section 3a names and protects that behaviour; it does not redesign it. The reference results (section 8) are taken from the current consolidation first, and the reconcile step must reproduce them exactly before anything changes.

**What this guards against.** Double counting (the same kWh from two sources), partial data presented as whole, one source silently overwriting another, and a match that is wrong with no way to see why. Each reconciled item keeps the list of observations behind it.

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
2. Reference results.
3. Core TeslaCam + drives (video-built drives, registry, processing record) - the new heart.
4. Tessie provider merged into the drive record.
5. Charging domain and the remaining providers (AGL, Evnex, solar, fuel, chargers), one at a time.
6. Task runner and Activity panel in the app; CLI wrappers generated from the same task definitions.
7. Retire every old script once its task passes the reference results.

## 10. Open decisions

- Per-field merge rule when video and Tessie disagree (section 3), and the match rules and confidence for drives and charge events (section 3a).
- Whether `drives_master.csv` keeps its name and columns, or becomes a store of per-day JSON with an export.
- Package names and folder layout (section 6).
- Domain order after the core (section 9).
- Whether to run one cloud agent per domain once this is agreed.
