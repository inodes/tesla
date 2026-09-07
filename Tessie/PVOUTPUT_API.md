# PVOutput API — Notes for Future Sessions

> Quick-orientation doc so a future session (agent or human) doesn't have to
> re-research this from scratch. Official reference, always authoritative:
> **https://pvoutput.org/help/api_specification.html**. That page is
> disallowed by `robots.txt`, so this session could not fetch it directly —
> but the user pasted its full real content into the conversation, so
> everything below (except the small "What still needs verifying" list at
> the bottom) is now sourced from the actual official spec, not secondary
> guesswork. An earlier pass of this doc was pieced together from secondary
> sources (a Python client's own docs, its source code, a PyPI page, and a
> forum thread) and got the auth header names wrong — see REQ-039 in
> `AGENTS.md` for the full correction history.

## Why this exists

The user has a PVOutput account tracking their home solar system's real
generation. The goal (see AGENTS.md REQ-027 and the AGL Solar VIP tariff
work) is a per-session "true opportunity cost of self-consumed solar"
calculator for home/Evnex EV charging: right now self-consumption can only
be inferred indirectly (Evnex's own solar % for a charging session, cross-
checked against whether AGL's smart meter shows simultaneous export).
PVOutput gives the other half of that picture directly — actual total solar
generation for the home, independent of what AGL's meter sees (net
metering means self-consumed solar never touches AGL's meter at all, so
`generation − AGL export = actual self-consumption`, computed exactly
instead of inferred).

## Auth — confirmed

Every request needs both an **API key** and a **System ID** (generate/find
these in the PVOutput account's own settings page, logged into
pvoutput.org). Two equivalent auth styles, both officially documented:

- **Headers** (preferred): `X-Pvoutput-Apikey: <API key>`,
  `X-Pvoutput-SystemId: <system id>` on every request.
  ⚠️ An earlier pass of this doc guessed `X-Pid`/`X-Apikey` from secondary
  sources — that was **wrong**. The real header names are
  `X-Pvoutput-Apikey` and `X-Pvoutput-SystemId`.
- **Query parameters** (also officially valid, GET requests only):
  `key=<API key>`, `sid=<system id>`.
- A **read-only** API key can be generated separately from a full
  read/write key, for scripts (like this one will be) that should never be
  able to call the `add*.jsp`/`postsystem.jsp` write endpoints.
- Donation-tier accounts (a small annual donation to PVOutput) unlock
  higher rate limits and extended fields — see Rate Limits below.

**Config key convention** (matching this repo's existing flat
`Tessie/config.json` style — `vin`, `tessie_access_token`,
`evnex_username`/`evnex_password` all already live there regardless of
which domain they belong to): stored as `"pvoutput_api_key"` and
`"pvoutput_system_id"`. Placeholders added to `Tessie/config.json` and
`Tessie/config.example.json` in REQ-038; the user populated the real
values afterwards, and both have now been exercised against the real
account (see "Confirmed live against the real account" below).

## Rate limits — confirmed

- **Free account**: 60 requests/hour.
- **Donation account**: 300 requests/hour, plus extended fields (`ext=1`)
  and other donation-gated options on some endpoints.
- **Get Statistic is a separate, lower limit**, on top of the general one
  above: **12 requests/hour (free)**, **60 requests/hour (donation)**. Not
  in the earlier secondary-sourced pass of this doc at all — easy to blow
  through if `getstatistic.jsp` is called in a loop like `getstatus.jsp`
  might be.
- **Rate-limit response headers**: only returned when the request itself
  sends the header `X-Rate-Limit: 1`. The response then carries:
  - `X-Rate-Limit-Remaining` — requests left in the current window
  - `X-Rate-Limit-Limit` — the window's total allowance (NOT
    `X-Rate-Limit-Total`, which an earlier secondary-sourced guess used)
  - `X-Rate-Limit-Reset` — Unix timestamp (UTC) when the window resets
- **Practical implication for a sync tool**: on a free account, backfilling
  a full year of history one day per request (`getstatus.jsp?h=1&d=...`) is
  ~365 requests — over 6 hours at the free 60/hour cap. A donation account
  (300/hour) would do it in about 75 minutes. Always send `X-Rate-Limit: 1`
  from a sync script and back off using the returned headers rather than
  guessing at timing.

## Base URL & endpoint list — confirmed

Base URL: `https://pvoutput.org/service/r2/<endpoint>.jsp`

The endpoints actually relevant to this project:

| Endpoint | Purpose | Notes |
|---|---|---|
| `getstatus.jsp` | Live status, or one historical day's intraday readings, or day-level statistics | **The one that matters most** — see below for its three response shapes. |
| `getoutput.jsp` | One row per day: end-of-day summary | See below for confirmed params/fields. |
| `getstatistic.jsp` | Aggregate statistics over a date range | Separate, lower rate limit — see above. |
| `getsystem.jsp` | System metadata (name, capacity, panels, inverter, tariffs, status interval) | One-off/rarely-changing — worth caching rather than re-fetching every sync. |
| `getextended.jsp` | Daily extended (v7–v12) parameter data | Donation-required. Only relevant if the user's system logs custom v7–v12 fields (temperature, humidity, etc.) and those are wanted per-day rather than per-reading. |
| `getmissing.jsp` | Dates with missing output data in a range | Handy for a sync script to know what to backfill without diffing everything itself. |
| `getladder.jsp` | Ranking/leaderboard totals | Not relevant to this project. |
| `getfavourite.jsp` | The account's list of favourited systems | Not relevant to this project. |
| `search.jsp` | Search public PVOutput systems by location/country | Not relevant to this project. |
| `getteam.jsp` / `jointeam.jsp` / `leaveteam.jsp` | Team membership services | Not relevant to this project. |
| `getsupply.jsp` | Regional/grid-wide aggregate supply data | Confirmed **not** user-specific — irrelevant here. |
| `registernotification.jsp` / `deregisternotification.jsp` | Push-notification registration | Not relevant to this project (no mobile client here). |
| `addstatus.jsp` / `addoutput.jsp` / `addbatchstatus.jsp` | **Write** — upload readings | Not needed — the user's PVOutput system already uploads its own readings via its inverter/monitoring hardware. This integration is read-only; use a read-only API key. |
| `postsystem.jsp` | **Write** — update system configuration/extended-parameter config | Not needed, same reason as above. |
| `deletestatus.jsp` | **Write** — delete a status entry | Not needed. |

## `getstatus.jsp` — confirmed parameters and response shapes

Parameters: `d` (date, `YYYYMMDD`, system-local) · `t` (time) · `h=1`
(History — pulls a full historical day of intraday readings instead of
just today's live status) · `asc=1` (ascending order) · `limit` (default
**30**, max **288** = one reading per 5 minutes for a full day) · `from` /
`to` (time-of-day bounds within the day) · `ext=1` (extended v7–v12
fields, donation-required) · `sid1` (a specific system, donation-required)
· `stats=1` (Day Statistics — a distinct response shape, see below).

This is the endpoint that matters most for the opportunity-cost
calculator: `h=1&limit=288` pulls a full historical day of intraday
generation readings at the system's own Status Interval, which can be
aggregated to half-hourly to line up against `agl_usage_master.csv`'s
half-hourly export rows.

**Three different response shapes depending on the flags used:**

1. **Basic (no `h`, no `stats`)** — today's live status, one row. Confirmed
   real example: `20210228,13:00,359,731,92,130,0.164,21.4,240.4`. Fields
   in order: Date, Time, Energy Generation (Wh), Power Generation (W),
   Energy Consumption (Wh), Power Consumption (W), **Normalised Output
   (kW/kW)**, Temperature (°C), Voltage (V), then v7–v12 appended if
   `ext=1`.

2. **History (`h=1`)** — one row per interval for the requested day. The
   field order is **different from the basic shape** — confirmed real
   example: `20200228,10:40,359,0.080,731,732,0.164,92,130,NaN,NaN`. Fields
   in order: Date, Time, Energy Generation, **Energy Efficiency
   (kWh/kW)**, Instantaneous Power, Average Power, Normalised Output,
   Energy Consumption, Power Consumption, Temperature, Voltage, then
   v7–v12 if `ext=1`. (The two `NaN`s in the example are Temperature/
   Voltage not being logged for that reading.)

3. **Day Statistics (`stats=1`)** — a single summary row for the day, in
   three semicolon-separated sections. Confirmed real example:
   `359,731,731,10:40;92,130,104,10:00;10,30,20`. Sections: **generation**
   (Energy Generated, Power Generated, Peak Power, Peak Power Time),
   **consumption** (Energy Consumed, Power Consumed, Standby Power,
   Standby Power Time — owner-account-only), **temperature** (Min, Max,
   Average). This is a new feature not present in the earlier
   secondary-sourced pass of this doc at all.

## `getoutput.jsp` — confirmed parameters and fields

Parameters: `df` (date from, `YYYYMMDD`) · `dt` (date to, `YYYYMMDD`) ·
`a` (aggregate: `'m'` monthly or `'y'` yearly) · `limit` · `tid` (team id)
· `sid1` (donation-required) · `insolation=1` (adds an Insolation field) ·
`timeofexport=1` (adds Export time-of-use fields alongside Import).

Confirmed real response field order: Date, Energy Generated, Efficiency,
Energy Exported, Energy Used, Peak Power, Peak Time, Condition, Min Temp,
Max Temp, then Peak/Off-Peak/Shoulder/High-Shoulder **Import** fields,
then (if `timeofexport=1`) the equivalent **Export** time-of-use fields,
then (if `insolation=1`) an Insolation field.

**Restriction**: max **50 records per call on a free account**, **150 on a
donation account** — not 366 as an earlier secondary source implied.

## `getstatistic.jsp` — confirmed fields

Parameters: `df`, `dt` (date range) · `c=1` (include consumption/import
figures) · `crdr=1` (include credit/debit amounts, in the account's
configured currency) · `sid`.

Confirmed fields: Energy Generated, Energy Exported, Average Generation,
Minimum Generation, Maximum Generation, Average Efficiency, Outputs
(count of days with data), Actual Date From, Actual Date To, Record
Efficiency, Record Efficiency Date. If `c=1`: also Energy Consumed, Peak
Import, Off-Peak Import, Shoulder Import, High-Shoulder Import, Average
Consumption, Minimum Consumption, Maximum Consumption. If `crdr=1`: also
Credit Amount, Debit Amount.

Remember: this endpoint has its own tighter rate limit (12/hr free, 60/hr
donation) — see Rate Limits above.

## `getsystem.jsp` — confirmed fields

Parameters: `sid` · `array2=1` (secondary array) · `donations=1`,
`teams=1`, `est=1`, `tariffs=1`, `ext=1` (flags controlling which optional
blocks are included).

Confirmed fields include: system name, DC capacity, install date, panel
brand/model, panel count, inverter brand/model, orientation, tilt,
shade %, latitude/longitude, **Status Interval (minutes)** — this
confirms the system's actual live-data reporting cadence, useful for
knowing how finely `getstatus.jsp?h=1` data can be trusted — plus (if the
flags are set) a semicolon-separated tariff block (Export Tariff, Import
Peak/Off-Peak/Shoulder/High-Shoulder Tariff, Import Daily Charge, all in
cents), team memberships, donation count, extended-data (v7–v12) field
config, and monthly generation estimates (kWh).

**Confirmed real example** (`getsystem.jsp?donations=1` against the
user's own account): 16 comma-separated fields — system name, size (W),
postcode, panel count, panel power (W), panel brand, inverter count,
inverter power (W), inverter brand, orientation, tilt, shade, install
date, latitude, longitude, then a final semicolon-separated block
`"5;;1"` = Status Interval (5 min) `;` Secondary array Status Interval
(blank — no `array2`) `;` Donations (1 — confirms **donation-tier**,
matching the 300 req/hour seen in the rate-limit headers below, not the
free tier's 60).

## Error catalog — confirmed

Common errors returned by the service:

- **400** — invalid date, date too old, or date in the future.
- **400** — generation/export/consumption/peak-power value exceeds what
  the configured system size makes plausible (a system-size mismatch
  check).
- **400** — `"Moon Powered"` — generation logged at a time flagged as
  night for the system's configured location/timezone; usually means the
  system's timezone configuration is wrong, not real data.
- **400** — min/max temperature values must be supplied as a pair (one
  without the other is rejected).
- **405** — wrong HTTP method for the endpoint (e.g. GET on a
  write-only/POST endpoint).
- **401** — invalid or disabled API key or system ID.
- **403** — a read-only API key was used against a write endpoint, or the
  account's rate limit was exceeded, or the endpoint/option is
  donation-mode-only and the account isn't donation-tier.

## Fit with existing AGL/solar work

- `agl_usage_master.csv` already has half-hourly `import`/`solar_export`/
  `controlled_load` rows (AGL's smart meter, REQ-031). `getstatus.jsp?h=1`
  history, aggregated to the same half-hourly buckets, gives **actual
  total generation** for the same windows —
  `self_consumed_kwh = generation_kwh − solar_export_kwh` for any
  half-hour, computed directly rather than inferred from Evnex's own
  solar % estimate or a "no simultaneous export" proxy.
- This directly feeds the still-unbuilt AGL/solar opportunity-cost
  calculator (REQ-027's scoped-but-not-started 4-part plan: Evnex `.json`
  loader, AGL billing-cycle helper, per-session cost-split calc, display
  wiring — see AGENTS.md TODO-013) — PVOutput data replaces the "no
  simultaneous export" proxy in step 3 with an exact self-consumption
  figure. The user explicitly sequenced this PVOutput work before the AGL
  calculator itself (AGENTS.md TODO-016).
- Timezone note: PVOutput's `d`/`df`/`dt` dates are system-local time per
  the account's configured timezone in its system settings (see
  `getsystem.jsp`'s config, and the `"Moon Powered"` error above, which
  exists specifically to catch a wrong timezone config) — this still needs
  confirming against the user's own real account before matching against
  AGL's own AEST/AEDT half-hourly rows the same way AGENTS.md TODO-009
  already handles for Tessie's own CSV timestamps.

## Confirmed live against the real account (AGENTS.md TODO-016)

Everything above was already sourced from the real, official API
specification the user pasted directly (see REQ-039) — not secondary
guesses. The remaining account-specific facts have now been confirmed by
a real `Tools/test_pvoutput_api.py --show-values` run against the user's
own account (system `...1754`, 10.5kW / 30 panels):

1. **Account tier: donation-tier, confirmed.** The rate-limit header
   came back `Limit=300` (not the free tier's 60), and `getsystem.jsp`'s
   own Donations field read `1` — see the confirmed example above.
2. **`getstatus.jsp?h=1` field order: confirmed exactly**, reading matches
   the documented History-shape order field-for-field — e.g. a real
   23:55 row's Energy Generation (36486 Wh) divided by the system's own
   10500 W capacity lands exactly on that same row's Energy Efficiency
   field (3.475 kWh/kW), and Energy Consumption correctly reset to 0 at
   the first reading (00:00) of the next day.
3. **Rate-limit header names: confirmed exactly** — `X-Rate-Limit-Remaining`,
   `X-Rate-Limit-Limit`, `X-Rate-Limit-Reset` all came back as documented.
   One bug caught in `Tools/pvoutput_common.py` along the way: the first
   test run showed "no X-Rate-Limit-* headers in response" even though
   they were actually present — `print_rate_limit()` was doing a
   case-sensitive dict lookup against a plain `dict()` of the response
   headers, and HTTP header names are case-insensitive. Fixed with a
   lower-cased lookup; re-running then showed the real values
   (`Remaining=297, Limit=300, Reset=1788825600`).
4. **Rate-limit reset boundary: confirmed UTC**, not system-local — the
   real `X-Rate-Limit-Reset` value (`1788825600`) decodes to exactly
   `2026-09-08T00:00:00Z`, a clean UTC midnight.
5. **System-local timezone for `d`/`df`/`dt` dates: still not fully
   proven**, though circumstantially consistent with Australia/Sydney —
   the account's own configured location (per `getsystem.jsp`'s postcode
   and lat/lon fields, not repeated here - see AGENTS.md's Zero-PII rule)
   is in Sydney, NSW, and the pulled day's readings show generation
   dropping to near-zero by the evening hours as expected for that
   timezone. A firmer check (comparing a reading's exact solar-noon peak time against
   real local solar noon for that date) hasn't been done yet — low
   priority unless a future half-hourly match against AGL's AEST/AEDT
   rows (AGENTS.md TODO-009) shows an off-by-some-hours drift.
6. **First real registration/test call: done.** `Tools/pvoutput_common.py`
   + `Tools/test_pvoutput_api.py` now exist, mirroring the
   `tessie_api_common.py`/`test_tessie_api.py` pattern, and have been run
   twice successfully against the real account.
