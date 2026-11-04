"""short_place.py - the short place of a spot for the label on the video: "Main Beach, QLD".

TeslaCam first: the car writes a suburb into most of its clips and the clips carry GPS, so the label comes from the footage itself.
    1. the suburb the clip carries;
    2. else (a garage, a basement: the car wrote none) the suburb of the nearest clip that has one and a position, within `near_m` - offline, instant;
    3. else a lookup of the position (Nominatim, in the background, kept in a file beside the other data): the answer is there on the next ask.
The state is worked out from the position (au_state); close to a border (`border_deg`) it is looked up as well and the lookup wins once it is in.

    SP = ShortPlace(data_dir, geocode=None)       geocode(lat, lon) -> {"address": {...}} or None (default: places_common.reverse_geocode)
    SP.feed([(lat, lon, suburb), ...])            the clips that know their suburb and position
    SP.label(suburb, lat, lon)                    -> "Suburb, ST" | "ST" | "Suburb" | ""
"""
import json
import math
import os
import threading

import au_state

_STATES = {"New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD", "South Australia": "SA", "Western Australia": "WA", "Tasmania": "TAS",
           "Northern Territory": "NT", "Australian Capital Territory": "ACT"}
CELL = 0.005                    # grid cell in degrees (about 500 m)


def _hav(a, b, c, d):
    p = math.pi / 180.0
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742000.0 * math.asin(min(1.0, math.sqrt(x)))


class ShortPlace:
    def __init__(self, data_dir=None, geocode=None, near_m=600.0, border_deg=0.14, background=True):
        self.path = os.path.join(data_dir, "place_lookup_cache.json") if data_dir else None
        self.geocode = geocode
        self.near_m = near_m
        self.border_deg = border_deg
        self.background = background
        self.grid = {}
        self.cache = {}                         # "lat,lon" (4 places) -> [suburb, state]; [] = asked, no answer
        self.busy = set()
        self.lock = threading.Lock()
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.cache = json.load(f)
            except (OSError, ValueError):
                self.cache = {}

    # ------------------------------------------------------------------------------------------------ the clips' own suburbs
    def feed(self, items):
        grid = {}
        for lat, lon, sub in items:
            if sub and lat is not None and lon is not None:
                grid.setdefault((int(lat // CELL), int(lon // CELL)), []).append((lat, lon, sub))
        self.grid = grid

    def nearest(self, lat, lon):
        """The suburb of the nearest clip that has one, within near_m; None when there is none."""
        if lat is None or lon is None:
            return None
        ci, cj = int(lat // CELL), int(lon // CELL)
        best, bd = None, self.near_m
        for i in (ci - 1, ci, ci + 1):
            for j in (cj - 1, cj, cj + 1):
                for a, b, sub in self.grid.get((i, j), ()):
                    d = _hav(lat, lon, a, b)
                    if d <= bd:
                        best, bd = sub, d
        return best

    # ------------------------------------------------------------------------------------------------ a lookup of the position
    @staticmethod
    def _key(lat, lon):
        return "%.3f,%.3f" % (lat, lon)

    def _ask(self, lat, lon):
        key = self._key(lat, lon)
        try:
            geo = self.geocode
            if geo is None:
                import places_common as PM
                geo = PM.reverse_geocode
            a = (geo(lat, lon) or {}).get("address") or {}
        except Exception:
            a = {}
        sub = a.get("suburb") or a.get("city_district") or a.get("town") or a.get("village") or a.get("city") or ""
        st = (a.get("ISO3166-2-lvl4") or "").split("-")[-1] or _STATES.get(a.get("state") or "", "")
        with self.lock:
            self.cache[key] = [sub, st] if (sub or st) else []
            self.busy.discard(key)
            if self.path:
                try:
                    tmp = self.path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self.cache, f, separators=(",", ":"))
                    os.replace(tmp, self.path)
                except OSError:
                    pass

    def looked_up(self, lat, lon, ask=True):
        """(suburb, state) of the position from the lookup; None while it has not been asked (it is asked now, in the background) or when it gave nothing."""
        if lat is None or lon is None:
            return None
        key = self._key(lat, lon)
        with self.lock:
            hit = self.cache.get(key)
            if hit is None and ask and key not in self.busy:
                self.busy.add(key)
            else:
                ask = False
        if hit is None and ask:
            if self.background:
                threading.Thread(target=self._ask, args=(lat, lon), daemon=True).start()
            else:
                self._ask(lat, lon)
                hit = self.cache.get(key)
        return tuple(hit) if hit else None

    # ------------------------------------------------------------------------------------------------ the label
    def label(self, suburb, lat, lon):
        suburb = (suburb or "").strip()
        state = au_state.state(lat, lon) if lat is not None and lon is not None else None
        if lat is None or lon is None:
            return suburb
        doubt = au_state.near_border(lat, lon, self.border_deg)
        if not suburb:
            suburb = self.nearest(lat, lon) or ""
        if not suburb or doubt:
            got = self.looked_up(lat, lon)
            if got:
                suburb = suburb or got[0]
                if doubt and got[1]:
                    state = got[1]
        return ", ".join(x for x in (suburb, state) if x)
