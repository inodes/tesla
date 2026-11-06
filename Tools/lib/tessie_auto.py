"""tessie_auto.py - when the app should run a Tessie sync by itself (drives and charges), so nobody has to press Sync.

A sync is due when Tessie is set up, nothing else that reads or writes the drives is busy (idle), and
  * it has never been run, or its last clean finish is older than `gap_s` (this includes an app that was closed while it ran: nothing was recorded, so it runs again on the next open);
  * or the last try failed or was cut short: it is tried again after a wait that doubles each time (`RETRY_S` * 2^fails, at most `gap_s`), so an interruption (the network, a sleep, a crash)
    heals by itself without hammering Tessie;
  * unless the person pressed Stop: that one is left alone for a whole `gap_s`.

    due(now, ok_at, tried_at, fails, gap_s, stopped=False) -> bool
    outcome(status) -> "running" | "ok" | "stopped" | "failed" | "none"   (status: the ChargerSync status dict)
"""
RETRY_S = 300.0


def outcome(status):
    s = status or {}
    if s.get("running"):
        return "running"
    if s.get("ended") is None:
        return "none"
    if s.get("rc") == 0:
        return "ok"
    if any("stopped from the app" in str(x) for x in (s.get("lines") or [])[-5:]):
        return "stopped"
    return "failed"


def due(now, ok_at, tried_at, fails, gap_s, stopped=False):
    if gap_s <= 0:
        return False
    if tried_at and fails:
        return now - tried_at >= min(gap_s, RETRY_S * (2 ** max(0, fails - 1)))
    last = max(ok_at or 0.0, tried_at if stopped else 0.0)
    return not last or now - last >= gap_s
