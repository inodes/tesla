"""page_first.py - background work gives way while a page's request is being answered.

Python runs one thread at a time. A long build in the background (the look at the kept clips: seconds of pure Python) and a request that talks to sqlite or the
disk share the interpreter: each row or file the request reads hands it back, and gets it again only when the build lets go (every switch interval). A request
of a few hundred rows that took 0.3 s alone took 10-20 s beside such a build. So the server marks the requests it is answering, and a background build asks
`give_way()` at safe points in its loops: while another thread is answering a request it sleeps (a few milliseconds at a time, at most `max_wait`), so the
request runs alone and the build carries on after it.

    with serving():          around the answer to one request (tesla_app)
    give_way(max_wait=2.0)   in a background loop: waits while ANOTHER thread is answering a request; returns at once otherwise (cheap: call it often)
"""
import contextlib
import threading
import time

_SERVING = set()            # the threads answering a request now
_LOCK = threading.Lock()
_LAST = [0.0]


@contextlib.contextmanager
def serving():
    me = threading.get_ident()
    with _LOCK:
        _SERVING.add(me)
    try:
        yield
    finally:
        with _LOCK:
            _SERVING.discard(me)


def busy():
    """True while a thread other than this one is answering a request."""
    s = _SERVING
    return bool(s) and (len(s) > 1 or threading.get_ident() not in s)


def give_way(max_wait=2.0, step=0.005):
    """Sleeps while another thread answers a request, up to max_wait seconds. Checked at most every 2 ms (a loop may call it for each item)."""
    now = time.monotonic()
    if now - _LAST[0] < 0.002:
        return
    _LAST[0] = now
    end = now + max_wait
    while busy() and time.monotonic() < end:
        time.sleep(step)
