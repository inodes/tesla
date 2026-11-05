"""perf_log.py - how long the app takes, as it is used: one line per event in ~/Library/Logs/tesla_perf.log (kept under 1 MB: the older half goes to
tesla_perf.log.1). TESLA_PERF_LOG names another file (tests); an empty value turns the log off.

    mark(name)                      a start-up step: 'setup done', 'server bound', 'first request', with the seconds since the process was launched
    request(path, secs, nbytes)     an /api request that took more than SLOW seconds, with the bytes it sent
    client(d)                       what the page measured (POST /api/perf): {route, ms (route start to page drawn), first_ms (performance.now() at the first page)}

Lines are tab-separated: local time, kind, then key=value fields. Nothing is ever read back by the app. since_launch_s comes from ps, which counts whole
seconds, so it can read up to a second low (a start under a second reads 0.00); the time between marks of one run is exact.
"""
import os
import subprocess
import threading
import time

SLOW = 0.15
MAX_BYTES = 1 << 20
_LOCK = threading.Lock()
_T0 = [None]          # seconds the process had been running at import, and the clock then


def path():
    p = os.environ.get("TESLA_PERF_LOG")
    if p is not None:
        return p
    return os.path.expanduser("~/Library/Logs/tesla_perf.log")


def _etime(text):
    """'[[dd-]hh:]mm:ss' (ps -o etime) -> seconds."""
    text = (text or "").strip()
    days, _, rest = text.rpartition("-")
    parts = [int(x) for x in rest.split(":")]
    secs = 0
    for x in parts:
        secs = secs * 60 + x
    return secs + (int(days) * 86400 if days else 0)


def since_launch():
    """Seconds since this process was launched: ps's elapsed time read once (whole seconds), then this process's own clock."""
    if _T0[0] is None:
        try:
            out = subprocess.run(["ps", "-o", "etime=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5).stdout
            _T0[0] = (float(_etime(out)), time.monotonic())
        except (OSError, ValueError, subprocess.SubprocessError):
            _T0[0] = (0.0, time.monotonic())
    run, at = _T0[0]
    return run + time.monotonic() - at


def _write(kind, **kw):
    p = path()
    if not p:
        return
    line = "%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, "\t".join("%s=%s" % (k, str(v).replace("\t", " ").replace("\n", " ")) for k, v in kw.items()))
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            if os.path.exists(p) and os.path.getsize(p) + len(line) > MAX_BYTES:
                os.replace(p, p + ".1")
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def mark(name, **kw):
    _write("start", step=name, since_launch_s="%.2f" % since_launch(), **kw)


def request(path_, secs, nbytes):
    if secs > SLOW:
        _write("slow", path=path_, s="%.3f" % secs, bytes=nbytes)


def client(d):
    if not isinstance(d, dict):
        return
    kw = dict(route=str(d.get("route") or "")[:60])
    for k in ("ms", "first_ms"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            kw[k] = int(v)
    _write("page", **kw)
