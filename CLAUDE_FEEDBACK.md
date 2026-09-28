# CLAUDE_FEEDBACK.md — Antigravity Handover & Architecture Audit Log

> **⚠️ SUPERSEDED (2026-09-21):** This document is now a historical narrative log only.
> All still-open items from it have been migrated into real tracked rows in `AGENTS.local.md`
> (the single primary tracker for both agents - see its new "Section 0" banner). Read this
> file for the story of what happened; read `AGENTS.local.md` for current, actionable status.


**Date:** 2026-09-21  
**Author:** Antigravity (Google DeepMind)  
**Audience:** Claude (Cowork / Anthropic) & Developers picking up the `tesla` repository next  
**Canonical Spec Reference:** See `AGENTS.md` for master rules, table styling, and complete BUG/TODO tracker.

---

## 1. Executive Summary & Week in Review

Over the past week (2026-09-14 to 2026-09-21), this repository underwent major architectural expansion and stabilization across four primary domains:
1. **Multi-Network EV Charging Architecture & Discovery (`Tools/providers_chargers/` + `Tools/charger_find.py`)**: Generalization from Tesla-only into 10 first-party networks with co-location preservation, live pricing reconciliation, and facility scoring.
2. **OpenStreetMap (OSM) Live Compliance & Editing Suite (`Tools/places_osm_editor.py`, `Tools/lib/osm_client_common.py`, `Tools/lib/supercharge_common.py`)**: OAuth2 integration with OpenStreetMap API for automated auditing, micro-mapped stall node reconciliation, and human-compliant changeset authoring.
3. **Places Geocoding & Route Telemetry (`Tools/places_manager.py`, `Tools/sync_api_abrp.py`, `Tools/tessie_drives_analyzer.py`)**: Smart multi-candidate geocoding, ABRP route synchronization, adaptive drive profile smoothing, and deletion tombstone management.
4. **Tool Naming, Prefix Standardization & Provider Consistency (`Tools/`, `Tools/providers_chargers/`, `Tools/providers_power/`)**: Full systematic audit of prefixes (`analyse_`, `sync_`, `chargers_`, `power_`, `places_`) and archived utilities.

---

## 2. OSM Editing Policy Moving Forward (KISS Principles)

Per user directive, **no heavy OSM modifications should be executed moving forward**. Automated or bulk changes to geometries, surrounding highways, or deep tag hierarchies trigger friction with OSM community mappers.

### Core Tagging Checklist (Strict Focus)
When reviewing, auditing, or touching OpenStreetMap nodes, restrict tagging strictly to the core keys:
- **`amenity`**: `charging_station`
- **`brand`**: `Tesla Supercharger`
- **`brand:wikidata`**: `Q17089620`
- **`name`**: `Tesla Supercharger`
- **`short_name`**: `Tesla`
- **`operator`**: `Tesla`
- **`operator:wikidata`**: `Q478214`
- **`level`**: Preserve existing parking level if present (e.g. `1`, `0`, `-1`)

### Rules for OSM Edits:
1. **Simple, Human Descriptions Only**:
   Never use automated, bot-sounding, or verbose changeset descriptions. Use plain English:
   - `"Changed location to the correct location."`
   - `"Corrected the number of charging stalls - it was 6 but it's really 16"`
   - `"Changed metadata to be correct charger type (was V2 Supercharger, now V4 Supercharger)"`
   - `"Added missing charger details and sockets"`
2. **Surveyed GPS Lock on Micro-Mapped Stalls**:
   If an OSM site contains individual mapped stall nodes (`len(nodes) >= 2`), **the latitude and longitude of those nodes are physically surveyed and LOCKED**. Never reposition stall nodes to a station centroid.
3. **30m Divergence Threshold**:
   Compare OSM pin against `supercharge.info` physical stall coordinates. If $\le 30\text{m}$, consider it aligned (no repositioning needed). Expect official Tesla FindUs GPS to diverge because Tesla records driveway/property entrance points.

---

## 3. Investigation of supercharge.info as an Authoritative Source

We investigated the data structure, endpoints, and capabilities of **https://supercharge.info/** to determine how it fits into the repository architecture:

### A. Strengths & Coverage:
- **Gold Standard for Tesla Superchargers (DC Fast Charging)**:
  - Tracks all ~7,000+ global Tesla Supercharger hubs with daily community verification.
  - Accurate physical stall GPS coordinates (benchmarked against satellite imagery and ground surveys).
  - Explicit stall hardware breakdown (e.g. `16x V4`, `6x V2`, `8x V3`), total stall count, maximum power in kW (e.g. `250`, `300`), opening dates, elevation in meters, and Non-Tesla EV access flag (`otherEVs`).
  - Construction, permit, and expansion milestone histories via a per-site `changes` lookup (see below).
- **API Endpoints** (corrected 2026-09-26, verified against the live code rather than assumed):
  - `https://supercharge.info/service/supercharge/allSites` (Complete dataset, cached locally in `Tools/lib/supercharge_common.py` for 24h).
  - `https://supercharge.info/service/supercharge/changes?search=<name>` (Historical status events for one site by name; this is the real, working endpoint - the previously-documented `/allSiteChanges` 404s and doesn't exist. **Not cached** - unlike `allSites`, `fetch_site_changes()` hits the network on every call).

### B. Critical Limitation: Destination Chargers are NOT Tracked
- `supercharge.info` is **strictly dedicated to Tesla Superchargers (DC)**.
- It does **NOT** track or list Tesla Destination Chargers (AC Level 2 Wall Connectors at hotels, wineries, restaurants, shopping centers).
- **Source Separation Rule**:
  - **Tesla Superchargers**: Use `supercharge.info` for physical GPS, hardware version (V2/V3/V4), opening dates, and stall counts; use official Tesla FindUs for live pricing/TOU tariffs.
  - **Tesla Destination Chargers**: Must come directly from official Tesla FindUs (`findus.tesla.com`) or PlugShare/OSM.

---

## 4. Comprehensive Audit of Providers, Prefixes & Tool Naming

A thorough review was conducted across `Tools/`, `Tools/providers_chargers/`, `Tools/providers_power/`, `Tools/lib/`, and `Tools/archive/`.

### A. Prefix Standardization Map

| Prefix / Category | Directory | Scripts & Modules | Purpose & Conventions |
| :--- | :--- | :--- | :--- |
| **`analyse_*`** | `Tools/` | `analyse_battery_health.py`<br>`analyse_car_efficiency.py`<br>`analyse_charge_curves.py`<br>`analyse_charging.py`<br>`analyse_drives.py`<br>`analyse_session_cost.py` | Analytics, charting, financial reconciliation, and telemetry drill-downs (British English spelling). |
| **`sync_*`** | `Tools/` | `sync_all.sh`<br>`sync_charger_all.sh`<br>`sync_charging_all.sh`<br>`sync_dashcam.sh`<br>`sync_drives_all.sh`<br>`sync_fuel_all.sh`<br>`sync_api_evnex.py`<br>`sync_api_tessie.py`<br>`sync_fuel_history.py`<br>`sync_invoices.py`<br>`sync_power_usage.py` | Automated data fetching, API sync, and invoice ingestion wrappers. **Exception: `sync_api_abrp.py`** is a manual, one-time OAuth setup / vehicle-profile inspection tool despite the shared prefix - it is not chained into any `sync_*_all.sh` pipeline (see BUG-123). |
| **`chargers_*`** | `Tools/providers_chargers/` | `chargers_base.py`<br>`chargers_manager.py`<br>`chargers_ampcharge.py`<br>`chargers_bppulse.py`<br>`chargers_chargefox.py`<br>`chargers_evie.py`<br>`chargers_evx.py`<br>`chargers_exploren.py`<br>`chargers_jolt.py`<br>`chargers_nrma.py`<br>`chargers_plugshare.py`<br>`chargers_tesla.py` | 10 first-party charger network providers + core manager and data models. |
| **`power_*`** | `Tools/` & `Tools/providers_power/` | `power_cost_calculator.py`<br>`power_demand_calculator.py`<br>`power_base.py`<br>`power_agl.py` | Home power tariffs, AGL usage data, and solar/demand calculators. |
| **`places_*`** | `Tools/` | `places_manager.py`<br>`places_osm_editor.py` | Geocoding, place clustering, and OSM audit/editing. |
| **`dashcam_*`** | `Tools/` | `dashcam_exportdash.sh`<br>`sync_dashcam.sh` | Dashcam footage syncing and ExportDash web visualizer container runner. |

### B. Clarification of Potentially Confusing Scripts

1. **`sync_charger_all.sh` vs `sync_charging_all.sh`**:
   - `sync_charger_all.sh`: Discovers and synchronizes **charging hardware / station registries** across all 10 networks (Tesla, Chargefox, Evie, AmpCharge, NRMA, etc.).
   - `sync_charging_all.sh`: Synchronizes **charging session telemetry** (Tessie charges, Evnex home charges, invoice matching, AGL usage).
2. **`dashcam_exportdash.sh` vs `sync_dashcam.sh`**:
   - `sync_dashcam.sh`: Syncs video clips from mounted USB volumes to local storage.
   - `dashcam_exportdash.sh`: Launches the Docker web viewer container.
3. **`Tools/archive/charger_find_tesla.py`**:
   - Archived legacy Playwright scraper retained because it possesses live web-scraping logic for new Tesla stations that has not yet been fully merged into `chargers_tesla.py` (tracked under REQ-095).
4. **`Tools/migrate_tesla_metadata.py`**:
   - One-off migration script for 6-field Tesla metadata. Can remain in `Tools/` or be archived if no longer actively run.

---

## 5. Major Capabilities Added (Past Week Details)

- **Unified Multi-Provider Charger Architecture (`Tools/providers_chargers/` + `Tools/charger_find.py`)**:
  - All 10 providers fully operational with honest re-verify diffs, operational status display, and facility scoring.
  - Co-location preservation: AC Destination and DC Supercharger records at the same venue are maintained as distinct entities rather than merged into a single inaccurate record.
- **Overpass POI & Facility Quality Scoring (`Tools/lib/station_facilities.py`)**:
  - 0–10 facility scoring engine evaluating nearby dining, retail, supermarkets, and restrooms.
  - Implemented geocoding latency caching and distance sorting from home/origin (`--near Home --sort distance`).
- **Telemetry & Drive Profiling (`Tools/analyse_drives.py`, `Tools/analyse_charging.py`)**:
  - Adaptive trip rolling window for elevation profiles and drive telemetry charts.
  - Regenerative braking shading and trip-average reference lines.
  - Statistical outlier rejection for battery degradation tracking with physical capacity ceilings.
  - Deletion tombstones (`--flagged` / `--staged` CLI flags).
- **ABRP (A Better Routeplanner) Telemetry Stream (`Tools/sync_api_abrp.py`, `Tools/lib/abrp_common.py`)**:
  - Live route planning, telemetry streaming, and SoC prediction.

---

## 6. Key Findings & What Broke (Lessons Learned)

| Issue / Breakage | Root Cause | Solution & Rule for Claude |
| :--- | :--- | :--- |
| **OSM Changeset Friction** | Verbose bot-like changeset comments and 4-URL source dumps triggered OSM community spam suspicion. | **Rule:** Generate plain, human, concise changeset comments describing only the real action (e.g. *"Changed location to the correct location."*). Keep sources to *"official Tesla website; supercharge.info; ground survey"*. |
| **Micro-Mapped Stall Coordinate Corruption Risk** | Automated repositioning tools attempted to move all stall nodes to the station center point. | **Rule:** When micro-mapped stall nodes are detected, lock latitude/longitude. Never alter physical stall GPS coordinates. |
| **Tesla Registry vs Physical Stall GPS Divergence** | Official Tesla Find Us API publishes the driveway/property entrance coordinates, not the physical charging bays. | **Rule:** Compare OSM GPS against `supercharge.info` physical stall coordinates with a 30m tolerance threshold. Expect Tesla directory GPS to diverge. |
| **False Merging of Distinct Providers** | Merging logic collapsed distinct charging providers sharing the same street address (e.g. NRMA + Tesla Destination). | **Rule:** Require matching provider/network ID before merging. Preserve multi-tier records at co-located sites. |
| **Silent Data Drop in Archive Loaders** | `load_json_registry()` skipped list-valued entries (`if not isinstance(v, dict): continue`), emptying historical tariff archives. | **Rule:** Archives legitimately store lists of snapshots for historical drift tracking. Always normalize list-valued entries instead of dropping them. |
| **Egress Block in Sandboxed VMs** | Sandbox environments block specific domains (`api.plugshare.com` or `api.tessie.com`) with `403 Forbidden` / `blocked-by-allowlist`. | **Rule:** Do not assume a 404 or empty response in sandboxed bash is an upstream API failure. Verify network accessibility with `curl` or prompt the user. |

---

## 7. Current Open Items & Handover Checklist (TODOs & BUGs)

1. **Absorb Tesla Scraper into `chargers_tesla.py` (REQ-095 / TODO)**:
   - Port the Playwright scraping routines from `Tools/archive/charger_find_tesla.py` into `Tools/providers_chargers/chargers_tesla.py` so `sync_charger_all.sh` does not depend on `archive/`.
2. **PlugShare Rate-Limiting Backoff (BUG-008 / TODO-006)**:
   - Progressive pacing and chunked limiters when refreshing missing prices across large regions.
3. **ABRP Telemetry Stream Live Validation (TODO)**:
   - Validate long-term token refresh and background sync stability for `Tools/sync_api_abrp.py`.
4. **Git Hygiene Reminder**:
   - Always run `git status` before concluding a session to verify no orphaned `.git/index.lock` or uncommitted scratch files remain.
   - Always test modifications with real data before calling work complete.

---

## 8. Key CLI Command Reference

```bash
# 1. Search chargers near location (with DC filter and distance sort)
./Tools/charger_find.py --near "Coffs Harbour" --network Tesla --radius 15 --dc

# 2. Audit a Tesla Supercharger on OpenStreetMap vs supercharge.info
./Tools/places_osm_editor.py --audit-tesla "Coffs Harbour, NSW"

# 3. Interactive OSM Place Editor
./Tools/places_osm_editor.py --places-editor

# 4. Sync All External Telemetry & Data Sources
./Tools/sync_all.sh

# 5. Review Drive Profiles & Battery Health
./Tools/analyse_drives.py --recent 10 --chart
./Tools/analyse_charging.py --health
```
