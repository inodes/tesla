"""car_watch.py - asks Tessie which software the car is on and keeps the answer (lib/car_versions.py): the version it is on, its release notes, and any update staged.

    check(data_dir, token=None, vin=None, api=api_get)    one look -> dict(version, new, staged, fsd) or dict(error=...)
    Watch(data_dir, minutes)                               looks again every `minutes` in a thread of the app (software.car_check_minutes in config.json, 0 = never)

Tessie answers from what it last heard from the car (it does not wake it), so a look is cheap. The sync scripts do the same look at the end of a drives sync
(sync_api_tessie.sync_car_version calls check()); the app's own watch makes sure an update is noticed the day it is taken, not at the next sync.
"""
import json
import threading
import time

from lib import car_versions


def check(data_dir, token=None, vin=None, api=None):
    if api is None:
        from lib.tessie_api_common import api_get as api
    if not token or not vin:
        from lib.tessie_api_common import get_token, get_vin, load_config
        cfg = load_config()
        token, vin = token or get_token(None, cfg), vin or get_vin(None, cfg)
    if not token or not vin:
        return dict(error="No Tessie access token and VIN are set up.")
    status, body = api("state", vin, token)
    if status != 200:
        return dict(error="Tessie answered HTTP %s." % status)
    try:
        state = json.loads(body)
    except ValueError:
        return dict(error="Tessie's answer was not JSON.")
    vs = state.get("vehicle_state") or {}
    car_version = (vs.get("car_version") or "").strip()
    staged = (vs.get("software_update") or {}).get("version") or None
    if not car_version:
        return dict(error="The car's version is missing from Tessie's answer (asleep or offline).")
    notes = None
    have = car_versions.load(data_dir)
    known = next((v for v in have["versions"] if v["version"] == car_version.split()[0]), None)
    if not known or not known.get("notes"):                 # the notes of a version are asked for once
        status, body = api("release_notes/", vin, token)
        if status == 200:
            try:
                j = json.loads(body)
                if car_version.split()[0] == str(j.get("deployed_version") or "").split(" ")[0]:
                    notes = j.get("release_notes") or None
            except ValueError:
                pass
    entry, new = car_versions.record(data_dir, car_version, notes=notes, staged=staged)
    return dict(version=entry["version"], new=new, staged=car_versions.parse_car_version(staged)[0] or None, fsd=entry.get("fsd"))


class Watch:
    def __init__(self, data_dir, minutes_fn):
        self.data = data_dir
        self.minutes = minutes_fn                    # read each time round, so a change in config.json is picked up
        self.last = None                             # the last answer of check()
        self.at = None

    def look(self):
        try:
            self.last = check(self.data)
        except Exception as e:                       # the watch never stops the app
            self.last = dict(error="%s: %s" % (type(e).__name__, e))
        self.at = time.time()
        return self.last

    def _loop(self):
        time.sleep(20)
        while True:
            try:
                gap = float(self.minutes() or 0) * 60
            except (TypeError, ValueError):
                gap = 0
            if gap > 0:
                self.look()
                time.sleep(gap)
            else:
                time.sleep(300)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
