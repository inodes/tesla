---
name: cloud-report-back
description: Use when finishing work in a cloud session on the Tesla repo, where there is no real config or data, to write the Paste back block for the owner to run on his Mac.
---

# Cloud report-back

**When to trigger:** ending a task or opening a PR from a cloud session for this repo.

The full procedure is `AGENTS.md` section 8. Follow it exactly; this skill only makes sure you do.

## Steps
1. Run everything possible with `TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata`.
2. End the PR description and the final message with **Paste back**: Done, Not run on real data, Run on your Mac (exact commands, each with what a good result looks like), Risks.
3. Never print or request secrets, tokens, addresses or coordinates.
4. Push your branch and open a PR into `merged`; never push to `merged`.
