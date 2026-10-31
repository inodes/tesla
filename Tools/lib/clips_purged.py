"""clips_purged.py - clips deleted with everything (video, metadata, stills, pictures, signs): they leave the TeslaCam list.

The footage log keeps a line for every file ever seen, even after the file is deleted (that is how "deleted from every drive" is counted), so a clip with nothing left
would still be listed as "Video deleted". When all five kinds are deleted from a clip, its minutes (a Recent recording) or its event (Saved, Sentry) are written here and
the list leaves them out.

    set_file(path)                 where they are kept (in the data folder, so the Macs share it)
    add(stamps, events)            stamps: [minute], events: [(kind, id)]
    has(stamp), has_event(kind, id)
"""
import json
import os
import threading
import time

_LOCK = threading.Lock()
_PATH = None
_S = set()
_E = set()


def set_file(path):
    global _PATH, _S, _E
    with _LOCK:
        _PATH = path
        for attempt in range(10):                       # a file the sync service has not let go of yet is asked for again, not taken as empty
            try:
                with open(path, encoding="utf-8") as f:
                    j = json.load(f)
                _S = set(j.get("stamps") or [])
                _E = {tuple(x) for x in (j.get("events") or []) if len(x) == 2}
                return
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.3)
            except (ValueError, AttributeError, TypeError):
                break
        _S, _E = set(), set()


def add(stamps, events=()):
    global _S, _E
    with _LOCK:
        _S = _S | set(stamps)
        _E = _E | {tuple(x) for x in events}
        if not _PATH:
            return
        try:
            os.makedirs(os.path.dirname(_PATH), exist_ok=True)
            with open(_PATH + ".tmp", "w", encoding="utf-8") as f:
                json.dump(dict(stamps=sorted(_S), events=sorted(_E)), f, separators=(",", ":"))
            os.replace(_PATH + ".tmp", _PATH)
        except OSError:
            pass


def has(stamp):
    return stamp in _S


def has_event(kind, eid):
    return (kind, eid) in _E


def version():
    """Changes when the set of clips taken off the list does (a cached answer worked out with another set is looked at again)."""
    return len(_S) * 100003 + len(_E)
