"""fetch_ledger.py - what has been fetched from a source, and when, so a sync can leave the finished ones alone.

One small JSON file, <data>/fetch_ledger.json:  {kind: {key: {at, status, source, note}}}
    kind    what the record is about ("drive_path": a drive's point file from Tessie)
    key     the record's own id (a drive: "t<start UTC epoch>", the id the app gives a Tessie drive)
    status  "complete" (fetched, nothing more to ask), "empty" (the source answered and has nothing: do not ask again),
            "failed" (could not be fetched: ask again next time)

    FetchLedger(data_dir).record(kind, key, status, note="", source="Tessie")
    FetchLedger(data_dir).get(kind, key)      -> the record or None
    FetchLedger(data_dir).settled(kind)       -> {keys that need no more asking}  (complete or empty)

Stores that fetch (the drive paths now; charge curves, footage and stills next) record here as they finish a record, and the Drives page and the syncs read it.
"""
import json
import os
import threading
import time

_LOCK = threading.Lock()
SETTLED = ("complete", "empty")


class FetchLedger:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "fetch_ledger.json")

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                j = json.load(f)
                return j if isinstance(j, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, kind, key):
        return (self._load().get(kind) or {}).get(key)

    def settled(self, kind):
        return {k for k, v in (self._load().get(kind) or {}).items() if v.get("status") in SETTLED}

    def record(self, kind, key, status, note="", source="Tessie"):
        with _LOCK:
            j = self._load()
            j.setdefault(kind, {})[key] = dict(at=int(time.time()), status=status, source=source, note=note)
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(j, f, separators=(",", ":"))
                os.replace(tmp, self.path)
            except OSError:
                pass
