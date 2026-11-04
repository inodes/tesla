"""au_state.py - the Australian state or territory a position is in, from latitude and longitude alone (no lookup, no network).

The borders are drawn as the straight lines and the rough river lines they mostly are, so a point within a few hundred metres of a border can come out on the wrong side. That is fine for
a label ("Main Beach, QLD") and wrong for anything that needs the exact border. None is returned outside Australia.
    state(lat, lon) -> "QLD" | "NSW" | "ACT" | "VIC" | "TAS" | "SA" | "WA" | "NT" | None
"""


def _qld_nsw_lat(lon):
    """Latitude of the Queensland / New South Wales border at a longitude: 29 S inland, then the Dumaresq and Macintyre rivers to Point Danger (28.17 S, 153.55 E)."""
    if lon <= 150.0:
        return -29.0
    return -29.0 + (-28.17 + 29.0) * (min(lon, 153.6) - 150.0) / 3.55


_MURRAY = ((141.0, -34.03), (142.15, -34.19), (143.55, -35.34), (144.75, -36.13), (145.65, -35.92), (146.0, -36.02), (146.92, -36.11), (147.9, -36.2), (148.1, -36.55), (149.98, -37.51))


def _nsw_vic_lat(lon):
    """Latitude of the New South Wales / Victoria border at a longitude (the Murray, then a straight line to Cape Howe): the state to the south of it is Victoria."""
    pts = _MURRAY
    if lon <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if lon <= x1:
            return y0 + (y1 - y0) * (lon - x0) / (x1 - x0)
    return pts[-1][1]


def state(lat, lon):
    if lat is None or lon is None:
        return None
    lat, lon = float(lat), float(lon)
    if not (-44.0 <= lat <= -9.0 and 112.0 <= lon <= 154.0):
        return None
    if lat < -39.4 and lon > 143.0:
        return "TAS"
    if lon < 129.0:
        return "WA"
    if lon < 138.0:
        return "NT" if lat > -26.0 else "SA"
    if lon < 141.0:
        return "QLD" if lat > -26.0 else "SA"
    if lat > _qld_nsw_lat(lon):
        return "QLD"
    if -35.95 <= lat <= -35.1 and 148.75 <= lon <= 149.4:
        return "ACT"
    if lon > 149.98 or lat > _nsw_vic_lat(lon):
        return "NSW"
    return "VIC"
