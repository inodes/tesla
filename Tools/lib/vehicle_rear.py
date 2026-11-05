"""vehicle_rear.py - is a round speed sign the one stuck on the back of a bus or truck (school buses: '40 when lights flash'; heavy-vehicle speed stickers)?

on_vehicle(bgr, x, y, r) -> bool, with x, y, r the sign's centre and radius in the pixels of the frame given. A vehicle's rear has a pair of red tail lamps, one each side of
the sign (which is on the middle of the rear), level with each other and a bus's or truck's width apart (2.2 to 2.6 m, so 3 to 5.5 sign radii each side of the sign; a car's lamps are about 1.4 m apart, 2 radii).
A sign on a post in front of a car does not show lamps that far apart, so it is not caught. Everything is in units of the sign's radius, so the frame can be any size.
"""
import cv2
import numpy as np

DX_MIN, DX_MAX = 3.0, 6.0       # a lamp's distance to the side of the sign's centre, in sign radii
DY_MIN, DY_MAX = -3.0, 10.0     # and below it (down is positive)
DY_LEVEL = 1.0                  # the two lamps are level with each other to within this
SYMMETRY = 1.0                  # and the sign is centred between them to within this (it is on the middle of the rear)
MIN_AREA = 0.02                 # a lamp is at least this fraction of the sign's area (a few pixels), so a speck of red is not one


def red_lamps(bgr, x, y, r):
    """Centres (cx, cy, area) of the bright red blobs around the sign, the sign's own ring left out."""
    h, w = bgr.shape[:2]
    x0, x1 = int(max(0, x - DX_MAX * r - 2)), int(min(w, x + DX_MAX * r + 2))
    y0, y1 = int(max(0, y + DY_MIN * r - 2)), int(min(h, y + DY_MAX * r + 2))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = (((hue <= 9) | (hue >= 170)) & (sat >= 110) & (val >= 120)).astype(np.uint8)
    yy, xx = np.ogrid[y0:y1, x0:x1]
    m[(yy - y) ** 2 + (xx - x) ** 2 <= (1.35 * r) ** 2] = 0        # the sign's own red ring
    n, _, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if a >= max(2, MIN_AREA * np.pi * r * r):
            out.append((cen[i][0] + x0, cen[i][1] + y0, int(a)))
    return out


def on_vehicle(bgr, x, y, r):
    lamps = red_lamps(bgr, x, y, r)
    left = [(cx, cy) for cx, cy, a in lamps if -DX_MAX * r <= cx - x <= -DX_MIN * r and DY_MIN * r <= cy - y <= DY_MAX * r]
    right = [(cx, cy) for cx, cy, a in lamps if DX_MIN * r <= cx - x <= DX_MAX * r and DY_MIN * r <= cy - y <= DY_MAX * r]
    for lx, ly in left:
        for rx, ry in right:
            if abs(ly - ry) <= DY_LEVEL * r and abs((lx - x) + (rx - x)) <= SYMMETRY * r:
                return True
    return False
