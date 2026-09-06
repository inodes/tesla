# CLAUDE.md — Claude (Cowork) Session Notes

> This file is Claude's own operating notes for working in this repo, kept alongside
> `AGENTS.md`. **`AGENTS.md` remains the canonical source of truth** for repo layout,
> table-formatting standards, the request/bug/TODO tracker, and the git/PII rules —
> read that first, every session. This file exists only to capture things specific to
> working here *as Claude in Cowork*, so they aren't rediscovered from scratch each time.

---

## 1. How this session reaches the repo

- This repo lives on the user's Mac at `~/repos/tesla`. Claude (Cowork) reaches it
  through a remote-device bridge, not a local clone — the bridge mounts it at
  `~/mnt/tesla` inside a sandboxed Linux VM that runs the `device_bash` tool. That VM
  is **not** the user's real shell: it has its own restricted network egress.
- **Known limitation**: this session's `device_bash` egress is blocked by a proxy
  allowlist for `api.plugshare.com` (confirmed via direct `curl` — returns
  `403 Forbidden` / `X-Proxy-Error: blocked-by-allowlist`). This means:
  - Running `find_plugshare_chargers.py --plugshare <id>` (or anything else that hits
    the live PlugShare API) from this session will silently report "not found" or
    empty data **even when the ID is valid**, because `PlugShareClient._get()`
    swallows non-404 HTTP errors (including this 403) and returns `None` — visually
    identical to a real 404.
  - **Do not trust a "not found" / empty result from this session as evidence the
    PlugShare ID or endpoint is broken.** Re-verify with the user running the same
    command in their own terminal (real network access) before concluding anything
    about the PlugShare API itself.
- A second cloud-container sandbox (used for scratch work, unrelated to
  `device_bash`) has separately been confirmed to also lack usable access to
  PlugShare's site (JS-rendered SPA; `WebFetch` only returns unreliable
  og:meta tags). Treat both paths as unable to independently verify live PlugShare
  data — the user's own browser/terminal is the only reliable source for that.

## 2. Working conventions specific to this session

- File edits on the repo happen via `device_bash` python heredocs doing exact
  substring match-and-replace with an `assert count == 1` guard before writing —
  never blind re-typing of file content from a prior (possibly truncated) tool read.
- After every edit: `python3 -m py_compile <file>` to catch syntax errors immediately
  (per `AGENTS.md` §7's own checklist).
- Per `AGENTS.md` §1.2, **never commit without first describing the change and the
  exact proposed commit message(s) to the user and getting a go-ahead** — this
  applies even though Claude, not Gemini Antigravity, is now the agent doing the work.
- `Tessie/*.json` (except `.example.json` templates) is gitignored, real personal
  data — treat as PII, never propose committing it, never paste its contents back to
  the user in full.

## 3. Session continuity log

Keep this section short — a running list of *in-flight* investigations that would
otherwise be lost between sessions. Move resolved items into `AGENTS.md`'s own Bugs /
Requests tables once closed out, and prune this list then.

- **Bunnings Gladesville (PlugShare charger) — RESOLVED**: turned out both PlugShare
  IDs (`801149` and `1517863`) are real, distinct chargers ~70m apart (a public 120kW
  Exploren DC unit, and a patron-only 22kW AC wall outlet) that happen to share the
  exact display name "Bunnings Gladesville". The user ran both live on their own
  machine and got real data for each — the earlier "not found"/empty results seen
  from this sandboxed session were entirely explained by the `api.plugshare.com`
  egress block (§1), not a real 404 or a missing-auth issue; that hypothesis is now
  ruled out. The *actual* bug (BUG-006 in `AGENTS.md`) was in
  `PlugShareRegistry.find_existing_match()`: it matched on exact name key before ever
  checking PlugShare ID, so saving the second station silently overwrote the first
  under the shared key and corrupted its `tariffs`/`keywords` via the
  existing-price-preservation merge in `add_or_update()`. Fixed both functions (ID
  agreement now required before a name/proximity match counts as "the same station";
  a create that would collide on key gets disambiguated instead of overwriting) and
  repaired the live `Tessie/plugshare_chargers.json` by splitting the corrupted single
  entry back into two correct ones, using the field values from the user's own
  live-scraped terminal output as ground truth. Verified with an offline unit test
  (two records, same name, different IDs → both survive) and by re-running
  `--inspect`/`--near Home --dc --radius 15` against the repaired JSON.
- **`--refresh-prices` (added; live-tested by the user, partially working)**: added to
  `find_plugshare_chargers.py` per the user's ask ("prices appear fixed on the ones
  you looked at but missing from a lot of others — could you re-run to pick up all
  prices?"). Root cause: bulk/region search (`--near`/`--state`/`--search`) never
  returns tariff data — only the single-station detail endpoint does. The user ran it
  live: most stations still came back unpriced. Verified via a mocked-network unit
  test that the refresh pipeline itself (missing-detection → fetch → merge → save →
  re-render) is correct, so the failure is almost certainly PlugShare rate-limiting a
  burst of ~20-30 rapid detail requests — manual one-at-a-time lookups (with natural
  pauses) succeed where the batch didn't. Added `PlugShareClient.last_error` (records
  *why* a request failed - HTTP code, timeout, non-JSON/challenge response - instead
  of a bare `None`) and jittered backoff, harder after a likely-429. This makes the
  failure visible and less aggressive but can't force PlugShare's throttling open; see
  BUG-008/TODO-006 in `AGENTS.md`. If the user reports it's still mostly failing after
  this, the next lever is spacing a full refresh across multiple separate invocations
  rather than one big burst (e.g. slicing with `--limit`), not more code cleverness.
- **`find_tesla_chargers.py` "Location / Suburb" column (BUG-007, resolved)**: its
  width floor (14) was narrower than the 18-cell header text, so the header overflowed
  its cell and sat flush against the border whenever no suburb name was long enough to
  push the column wider on its own. Fixed by flooring on `max(header_width, data_width)`
  like `find_plugshare_chargers.py` already did correctly. Verified with a synthetic
  table built from the exact suburb list in the user's own pasted output.
- **Commit grouping decision pending**: three logical changes are ready (table
  alignment rules across all analyzers incl. a currency `.3f`→`.2f` fix; default
  sort-by-state-then-name in the two charger explorers; TESLADRIVE-sync removal across
  4 files, including a real PII-exposure fix in `tessie_places.py`'s
  `sync_places_file()`). Awaiting user's call on committing as three, squashed into
  one, or holding until Bunnings Gladesville is fully resolved.
