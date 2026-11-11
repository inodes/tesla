"""tessie_auto.py - when the app should run a Tessie sync by itself (drives and charges), so nobody has to press Sync.

A sync is due when Tessie is set up, nothing else that reads or writes the drives is busy (idle), and
  * it has never been run, or its last clean finish is older than `gap_s` (this includes an app that was closed while it ran: nothing was recorded, so it runs again on the next open);
  * or the last try failed or was cut short: it is tried again after a wait that doubles each time (`RETRY_S` * 2^fails, at most `gap_s`), so an interruption (the network, a sleep, a crash)
    heals by itself without hammering Tessie;
  * unless the person pressed Stop: that one is left alone for a whole `gap_s`.

    due(now, ok_at, tried_at, fails, gap_s, stopped=False) -> bool
    order(kinds, missing) -> the kinds in the order to look at them: "drives" first while the dashcam has drives Tessie has not given (missing["cam_only"])
    missing_due(missing, now, tried_at, gap_s) -> True when what is missing calls for a drives sync now: dashcam-only drives every CAM_RETRY_S, drives without a route every gap_s
    write_off(ids, items, now) -> the dashcam-only drive ids a sync starting now may write off as "Tessie has no record of it": those that ended SETTLE_S or more ago
    outcome(status) -> "running" | "ok" | "stopped" | "failed" | "none"   (status: the ChargerSync status dict)
"""
RETRY_S = 300.0
SETTLE_S = 1800.0           # a drive that ended less than this long before a sync began may simply not be in Tessie yet: it is asked for again, not written off
CAM_RETRY_S = 600.0         # with dashcam drives Tessie has not given yet, the drives sync is tried again this often (until they are in, or old enough to be written off)


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


def order(kinds, missing):
    m = missing or {}
    return sorted(kinds, key=lambda k: 0 if (k == "drives" and m.get("cam_only")) else 1)


def missing_due(missing, now, tried_at, gap_s):
    m = missing or {}
    if gap_s <= 0:
        return False
    if m.get("cam_only") and now - (tried_at or 0.0) >= min(gap_s, CAM_RETRY_S):
        return True
    return bool(m.get("no_page")) and now - (tried_at or 0.0) >= gap_s


def write_off(ids, items, now):
    old = {v.get("id") for v in (items or []) if v.get("kind") == "drive" and (v.get("t1") or 0) <= now - SETTLE_S}
    return [i for i in (ids or []) if not str(i).startswith("t") and i in old]
