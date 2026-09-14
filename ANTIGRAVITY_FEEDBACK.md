# Feedback for Antigravity / Gemini: Regressions in the `find_chargers.py` Provider Port

**Date:** 2026-09-14
**Audience:** Whoever (or whichever model) picks up this codebase next under the "Antigravity" label
**Context:** You were asked to take the logic living in `Tools/find_tesla_chargers.py` (a single-network Tesla-only script with years of real fidelity baked in — dual-layer naming, TOU rate evaluation, historical archiving, live-scrape parity) and generalize it into a multi-provider architecture (`Tools/providers/*.py` + `Tools/find_chargers.py`) that could also cover Chargefox, PlugShare, Evie, EVX, and bp pulse.

The architecture itself — `BaseChargerProvider`, `UnifiedStationRecord`, `ChargerDiscoveryManager`, one file per network — is a reasonable shape and is being kept. The problem is execution: this port shipped with at least **thirteen distinct defects**, several of them outright crashes on first real use, and one of them a silent data-integrity bug severe enough to have quietly broken financial tariff reconciliation for however long it went unnoticed. None of these were exotic edge cases — they were the *first* real run of the ported code against real data. This document exists so the next pass doesn't repeat them.

If you are the one continuing this work: read every item below before touching `Tools/providers/` or `Tools/find_chargers.py` again. Several of these are the same *class* of mistake repeated across different files, which suggests a process problem, not just isolated typos — see the "Pattern" callouts.

---

## 1. Undefined variables that should never have compiled past a smoke test

Four separate `NameError`/`AttributeError` crashes shipped, each one a variable referenced but never defined or assigned:

- `Tools/providers/base.py` — `get_effective_pricing()` compared `s_num`/`e_num` against a time window without ever defining them (`NameError: name 's_num' is not defined`). Crashed on **any** station with an active time-of-use schedule — i.e. most of them.
- `Tools/providers/chargefox_provider.py` — called `resolve_au_state()` and `extract_suburb_from_station_name()` with neither function declared anywhere in the file. Crashed on **every** Chargefox load or query.
- `Tools/providers/plugshare_provider.py` — `record_dict_to_unified()` referenced `compat_dict` while evaluating the private-residential exclusion filter; it was never assigned. Crashed on **every** cached PlugShare record.
- `Tools/find_chargers.py` — the `--sync-tesla` handler referenced `args.unpriced`, a flag that was never added to that script's own `argparse` definition (it exists in `find_tesla_chargers.py`, which this was copied from, but the copy dropped the flag). Crashed on the exact `--sync-tesal --sc --renamed` command that had just been recommended to the user as the correct way to re-scrape.

**Pattern:** none of these would have survived a single real invocation of the affected code path. `python -m py_compile` does not catch a `NameError` — it only proves the file parses. Every one of these needed to actually be *run* once against real or representative data before being called done.

## 2. Filters and labels that quietly return the wrong answer instead of crashing

These are worse than the crashes above because nothing announces that they're wrong:

- **`--sc` leaked non-Tesla stations.** `filter_station_records()`'s supercharger-tier filter matched on `speed_tier == "supercharger"` alone. `plugshare_provider.py` was separately classifying `speed_tier` as `"supercharger"` whenever the station's *name* happened to contain the word "supercharger" — so a PlugShare-listed Evie station named "Coochin Creek Evie supercharger" passed straight through a flag whose entire documented purpose is "only look at `tesla_superchargers`." Two independent bugs compounding into one wrong result.
- **"Coming Soon" / "Under Construction" stations appeared in active search results.** Nothing filtered on status flags or on the `(Coming Soon)` / `(Under Construction)` title markers PlugShare itself provides. 69 unopened stations were sitting in the live PlugShare cache being presented as real, chargeable locations.
- **Access label disagreed with itself inside the same tool.** The table view's `access_label()` did an exact-string `self.source == "tesla"` check, which fails the moment a station is a merged multi-source record (`"tesla+chargefox"` for a site both networks list) — so a Tesla Destination Charger fell through to `Private` in the table. Meanwhile the deep-dive inspection card took the opposite shortcut and defaulted *every* non-Supercharger station to `Public (Open to All EVs)` regardless of what its own data said. Same station, two different, both-wrong answers depending which view you opened.
- **The "Sort Order" header lied about what actually happened.** `--sort` defaults to `"distance"` in argparse. The full-list and text-query code paths correctly *detected* that there's no reference point to sort by distance from and fell back to sorting by state or by name — but the header that describes the sort to the user kept reading the raw, unmodified `args.sort` value and printed "Distance (Closest First)" regardless, on every run that didn't pass `--sort` explicitly. The actual query logic was right; the label describing it to a human was wrong every single time it mattered.

**Pattern:** every one of these is a case where two pieces of code that are supposed to agree — a filter and a classifier, a table and a detail view, a query and its own display label — were written independently instead of sharing one source of truth, and nothing checked that they actually agreed before shipping.

## 3. Real fidelity lost in the port, not just new bugs introduced

This is the port's core mandate — "take what `find_tesla_chargers.py` already does well and generalize it" — and on a direct side-by-side comparison against the original script, the new path was strictly worse on at least five points for the *exact same station data*:

- **Short ID regressed to a meaningless number.** The registry key / short ID that `tessie_charging_analyzer.py` depends on for invoice matching came out as the raw upstream numeric `findus_id` (e.g. `31617`) instead of a human-readable, file-safe name (`New_Italy_NSW`). This isn't cosmetic — it breaks a downstream consumer that keys off this exact field.
- **The "Non-Tesla Flat Rate" section vanished entirely** from the inspection card, even though the underlying data (`tariffs.non_tesla.rate_schedules`) was still present in the registry — it just wasn't extracted or displayed anymore.
- **"Effective Date (Verified: ...)"** — a historically meaningful pairing that tells you both when a tariff took effect and when it was last confirmed — was flattened down to a bare, less useful "Last Verified."
- **The Data Source field lost its color coding**, and a **Find Us URL that fit comfortably in the available width got truncated anyway** (the `max_len` the new card passed was simply smaller than it needed to be).
- **The dual-layer naming distinction (`findus_list_name` vs `findus_location_name`) that a previous request explicitly built into the schema was not carried through into the change-detection logic.** When a station's list-page grouping name changed upstream but its own location name didn't, the registry data was updated correctly — but the changelog shown to the user after a re-scrape said nothing about it, because the diff engine only ever compared the registry *key* (which never changes for this class of rename) and had no check at all for `findus_list_name` drifting on its own.

**Pattern:** a port that reads the old code for structure but doesn't diff its actual *output* against the new code's output, field by field, on the same real input, will lose fidelity exactly like this — silently, and only surfaced by a human noticing a field is missing from a card they'd seen before.

## 4. One data-integrity bug serious enough to deserve its own heading

`tessie_charging_analyzer.py`'s `load_json_registry()` filtered incoming entries with `if not isinstance(v, dict): continue`. That's fine for a registry where every key maps to one snapshot — but an **archived** registry's value legitimately becomes a **list** of dicts once a station has been re-verified more than once with a real change each time (the very history this whole system exists to preserve). The filter silently discarded every list-valued entry. By the time this was caught, the entire archived Tesla-supercharger registry — all 8 real historical entries — was loading as `{}`, completely empty, with no error, no warning, nothing.

This matters because `get_expected_tariff_rate()`'s `is_archived_tariff` flag and the "Tariff Audit Delta" reporting depend on that archive being readable to do real financial reconciliation against invoices. A loader bug with zero visible symptoms had been silently telling that logic "there is no historical data here" for however long it had been broken. If you or anyone else has relied on a Tariff Audit Delta output recently, treat it as suspect until re-run against the fixed loader.

**Pattern:** a loader that silently drops data it doesn't recognize the shape of is a liability by construction — it should have raised, logged a count of skipped entries, or asserted the shape it expected, instead of quietly returning less data than the file actually contains.

---

## What actually needs to change about how this gets done

1. **Run the new code against real data before calling a port "done."** Every crash in section 1 would have been caught by one real invocation per provider. A port is not finished when it compiles; it's finished when its actual output has been exercised.
2. **Diff old vs. new output on the same input, field by field.** Section 3 exists entirely because nobody compared what the old script's card showed for a station against what the new script's card shows for the *same* station. That comparison is cheap and would have caught every item in that section immediately.
3. **When two code paths need to agree (a filter and a classifier; a table and a detail view; a query and its label), give them one shared function to call, not two independent implementations that are supposed to happen to match.** Section 2 is entirely instances of this rule being skipped.
4. **A loader should never silently accept "I don't recognize this shape, I'll just drop it" as a normal code path**, especially for anything an archive or a financial-reconciliation feature depends on. Fail loud, or explicitly normalize every shape you know the data can take — don't filter-and-hope.

None of this is a request to redo the architecture — `Tools/providers/` is a fine shape for where this repo needs to go. It's a request to actually exercise the code you write against the data it's going to run on before handing it back.
