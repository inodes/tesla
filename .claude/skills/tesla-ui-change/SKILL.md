---
name: tesla-ui-change
description: Use before changing anything under Tools/lib/app_ui/ or any window of the Tesla Mac app, so every window keeps one look and behaviour.
---

# Tesla app UI change

**When to trigger:** editing `Tools/lib/app_ui/` or adding or changing a window, control or page.

## Steps
1. Plan first and get approval (global rule).
2. Read `docs/ui-guidelines.md`, especially section 11 "One look between windows", which lists the shared pieces to reuse. Reuse them; do not make a new look.
3. Walk its "Checklist before a page or control ships" and state each answer.
4. Terminal tables follow `AGENTS.md` section 2. Help text is user-facing (`AGENTS.md` section 1 item 5).
5. Run `python3 -m py_compile <file>` after each edit, then the tests: `python3 -m unittest discover -s tests` (with `TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata` and no real data). Run it from a direnv shell: the venv provides scipy, and without it the road-graph tests error.
6. Re-read the lines above and below every insertion (`AGENTS.md` section 7).
7. Say which behaviour you could not see running.
