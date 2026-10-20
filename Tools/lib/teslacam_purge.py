"""teslacam_purge.py - free space on a TeslaCam drive by deleting only the oldest video whose value has been kept.

A minute is offered for deletion only when teslacam_deletable calls it safe: on the Archive its telemetry, stills and drive snapshots are
kept (and its drive is synced from Tessie); on JOWUA or the Tesla USB the Archive has the same files, same sizes. Oldest first, Recent
clips only unless Saved / Sentry are asked for, and never the newest `keep_days` days. Planning changes nothing; delete() removes the
planned files and nothing else.

    free_bytes(root)                                  space free on the drive now
    plan(rep, role, need_bytes, keep_days, kinds)     dict(minutes, bytes, items, enough, skipped_unsafe_bytes)
    delete(plan, progress, stop)                      removes the files; returns (files removed, bytes freed, errors)
"""
import os
import shutil


def free_bytes(root):
    return shutil.disk_usage(root).free


def plan(rep, role, need_bytes, keep_days=3, kinds=("recent",), today=None):
    """rep = teslacam_deletable.report(..., detail=True). need_bytes: how much to free (<= 0 plans nothing)."""
    import datetime
    det = rep[role]["detail"]
    newest = max([d["stamp"][:10] for d in det] or [""])
    cut = ""
    if keep_days and newest:
        cut = (datetime.date.fromisoformat(newest) - datetime.timedelta(days=keep_days - 1)).isoformat()
    safe = sorted((d for d in det if d["safe"] and d["src"] in kinds and (not cut or d["stamp"][:10] < cut)), key=lambda d: d["stamp"])
    unsafe_bytes = sum(d["bytes"] for d in det if not d["safe"] and d["src"] in kinds)
    items, got = [], 0
    for d in safe:
        if got >= need_bytes:
            break
        items.append(d)
        got += d["bytes"]
    return dict(minutes=len(items), bytes=got, items=items, enough=got >= need_bytes, need=max(need_bytes, 0), available=sum(d["bytes"] for d in safe),
                skipped_unsafe_bytes=unsafe_bytes, first=items[0]["stamp"] if items else None, last=items[-1]["stamp"] if items else None)


def delete(pl, progress=None, stop=None):
    """Removes the planned files (oldest first). Returns (files removed, bytes freed, [errors])."""
    n = freed = 0
    errs = []
    for i, d in enumerate(pl["items"], 1):
        if stop and stop():
            break
        for p in d["paths"]:
            try:
                sz = os.path.getsize(p)
                os.remove(p)
                n += 1
                freed += sz
            except FileNotFoundError:
                pass
            except OSError as e:
                errs.append("%s: %s" % (p, e))
        if progress:
            progress(i, len(pl["items"]), d["stamp"])
    return n, freed, errs
