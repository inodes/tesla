# OSM editing policy and supercharge.info as a source

Reference for anyone touching OpenStreetMap nodes or using supercharge.info data in this repo. Moved from the 2026-09-26 handover note; text unchanged apart from heading levels.

## OSM Editing Policy Moving Forward (KISS Principles)

Per user directive, **no heavy OSM modifications should be executed moving forward**. Automated or bulk changes to geometries, surrounding highways, or deep tag hierarchies trigger friction with OSM community mappers.

#### Core Tagging Checklist (Strict Focus)
When reviewing, auditing, or touching OpenStreetMap nodes, restrict tagging strictly to the core keys:
- **`amenity`**: `charging_station`
- **`brand`**: `Tesla Supercharger`
- **`brand:wikidata`**: `Q17089620`
- **`name`**: `Tesla Supercharger`
- **`short_name`**: `Tesla`
- **`operator`**: `Tesla`
- **`operator:wikidata`**: `Q478214`
- **`level`**: Preserve existing parking level if present (e.g. `1`, `0`, `-1`)

#### Rules for OSM Edits:
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

## Investigation of supercharge.info as an Authoritative Source

We investigated the data structure, endpoints, and capabilities of **https://supercharge.info/** to determine how it fits into the repository architecture:

#### A. Strengths & Coverage:
- **Gold Standard for Tesla Superchargers (DC Fast Charging)**:
  - Tracks all ~7,000+ global Tesla Supercharger hubs with daily community verification.
  - Accurate physical stall GPS coordinates (benchmarked against satellite imagery and ground surveys).
  - Explicit stall hardware breakdown (e.g. `16x V4`, `6x V2`, `8x V3`), total stall count, maximum power in kW (e.g. `250`, `300`), opening dates, elevation in meters, and Non-Tesla EV access flag (`otherEVs`).
  - Construction, permit, and expansion milestone histories via a per-site `changes` lookup (see below).
- **API Endpoints** (corrected 2026-09-26, verified against the live code rather than assumed):
  - `https://supercharge.info/service/supercharge/allSites` (Complete dataset, cached locally in `Tools/lib/supercharge_common.py` for 24h).
  - `https://supercharge.info/service/supercharge/changes?search=<name>` (Historical status events for one site by name; this is the real, working endpoint - the previously-documented `/allSiteChanges` 404s and doesn't exist. **Not cached** - unlike `allSites`, `fetch_site_changes()` hits the network on every call).

#### B. Critical Limitation: Destination Chargers are NOT Tracked
- `supercharge.info` is **strictly dedicated to Tesla Superchargers (DC)**.
- It does **NOT** track or list Tesla Destination Chargers (AC Level 2 Wall Connectors at hotels, wineries, restaurants, shopping centers).
- **Source Separation Rule**:
  - **Tesla Superchargers**: Use `supercharge.info` for physical GPS, hardware version (V2/V3/V4), opening dates, and stall counts; use official Tesla FindUs for live pricing/TOU tariffs.
  - **Tesla Destination Chargers**: Must come directly from official Tesla FindUs (`findus.tesla.com`) or PlugShare/OSM.

---
