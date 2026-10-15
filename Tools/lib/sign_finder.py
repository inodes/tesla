"""Find round speed-limit signs (red ring, white face, black number) in a dashcam frame and read the number.

find_signs(bgr) -> [dict(x, y, r, text, conf, crop)]   (x, y, r in pixels of the frame given)
Uses OpenCV for the red-ring search and tesseract (digits only) for the number. A reading is kept only
when it is a plausible Australian limit (40..130 in steps of 10), so brake lights, tail lights and
other red things drop out.
"""
import subprocess
import tempfile
import os

import cv2
import numpy as np

LIMITS = {"40", "50", "60", "70", "80", "90", "100", "110", "120", "130"}


def _ocr(gray):
    """Digits in a small grayscale image, via the tesseract CLI. Returns (text, conf)."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.png")
        cv2.imwrite(p, gray)
        r = subprocess.run(["tesseract", p, "stdout", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789", "tsv"],
                           capture_output=True, text=True)
    best = ("", 0.0)
    for ln in r.stdout.splitlines()[1:]:
        f = ln.split("\t")
        if len(f) >= 12 and f[11].strip():
            try:
                c = float(f[10])
            except ValueError:
                continue
            if c > best[1] or not best[0]:
                best = (f[11].strip(), c)
    return best


# Australia drives on the left, so roadside signs are on the LEFT of the front camera view (and overhead gantries, which
# the car's own lane sits under, are not searched). Painted limits are found separately by find_road_marks().
ROI_LEFT = (0.0, 0.55, 0.22, 0.64)      # x0, x1, y0, y1 as fractions of the frame
ROI_RIGHT = (0.50, 1.0, 0.22, 0.64)     # secondary: signs for exit ramps / the other carriageway (no entry, resume speed)


def find_signs(bgr, min_r=12, max_r=140, roi=ROI_LEFT, a_thr=None, ocr=True):
    h, w = bgr.shape[:2]
    x0, x1, ty, by = int(w * roi[0]), int(w * roi[1]), roi[2], roi[3]
    y0, y1 = int(h * ty), int(h * by)
    roi = bgr[y0:y1, x0:x1]
    # "red" relative to the frame (the dashcam image has a strong green cast): Lab a* well above its median
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    ach = lab[:, :, 1].astype(np.int16)
    base = int(np.median(ach))
    # low light: the ring is barely redder than the road, so scale the threshold to this frame's own spread
    thr = a_thr if a_thr else int(min(16, max(6, 0.55 * (float(np.percentile(ach, 99.9)) - base))))
    mask = ((ach - base >= thr) & (lab[:, :, 0] > (25 if thr < 16 else 40))).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    allc, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for i, c in enumerate(allc) if hier[0][i][3] == -1]
    holes = {}                       # outer contour index -> (area, circularity) of its roundest hole
    for i, c in enumerate(allc):
        par = hier[0][i][3]
        if par >= 0:
            (hx, hy), hr = cv2.minEnclosingCircle(c)
            circ = cv2.contourArea(c) / (np.pi * hr * hr) if hr > 0 else 0
            if par not in holes or circ > holes[par][1]:
                holes[par] = (cv2.contourArea(c), circ)
    outer_idx = [i for i, c in enumerate(allc) if hier[0][i][3] == -1]
    out = []
    for oi, c in zip(outer_idx, cnts):
        a = cv2.contourArea(c)
        if a < 60:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        if r < min_r or r > max_r:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if not 0.78 <= bw / float(bh) <= 1.28:
            continue
        fill = float(cv2.countNonZero(mask[y:y + bh, x:x + bw])) / (np.pi * r * r)            # a ring covers ~0.25-0.6 of its circle, a blob ~0.8+
        if not 0.15 <= fill <= 0.72:
            continue
        # a speed sign is a RING: the inside of the circle is not red (a car's rear or a tail light is solid red)
        yy_, xx_ = np.ogrid[:mask.shape[0], :mask.shape[1]]
        inner = (yy_ - cy) ** 2 + (xx_ - cx) ** 2 <= (0.58 * r) ** 2
        if inner.sum() > 0 and float(mask[inner].mean()) / 255.0 > 0.30:
            continue
        cy += y0
        cx += x0
        ri = int(r * 0.74)
        xa, xb, ya, yb = int(cx - ri), int(cx + ri), int(cy - ri), int(cy + ri)
        if xa < 0 or ya < 0 or xb > w or yb > h:
            continue
        face = bgr[ya:yb, xa:xb]
        g = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        if g.size == 0:
            continue
        white = float((g > np.percentile(g, 60)).mean())
        if g.std() < 11:
            continue
        # digits: dark on light. Otsu, make digits black, scale up, white border
        gg = cv2.resize(g, (0, 0), fx=max(1.0, 70.0 / g.shape[0]), fy=max(1.0, 70.0 / g.shape[0]), interpolation=cv2.INTER_CUBIC)
        _, bw_ = cv2.threshold(gg, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        bw_ = cv2.copyMakeBorder(bw_, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        text, conf = _ocr(bw_) if ocr else ("", 0.0)
        red = float(ach[y:y + bh, x:x + bw][mask[y:y + bh, x:x + bw] > 0].mean()) - base
        pad = int(r * 1.25)
        xa2, xb2, ya2, yb2 = max(0, int(cx - pad)), min(w, int(cx + pad)), max(0, int(cy - pad)), min(h, int(cy + pad))
        ha = holes.get(oi)
        ringness = 1.0 if (ha and ha[1] >= 0.7 and ha[0] >= 0.2 * np.pi * r * r) else 0.0
        out.append(dict(x=int(cx), y=int(cy), r=int(r), text=text, conf=conf, white=round(white, 2), ok=text in LIMITS, red=round(red, 1), ringness=ringness,
                        crop=bgr[ya2:yb2, xa2:xb2]))
    out.sort(key=lambda d: (not d["ok"], -d["conf"]))
    return out


def find_signs_hough(bgr, min_r=14, max_r=70, min_ring=5.0, roi=None):
    """Second detector for low light (dusk, tunnels), where the red ring is barely redder than the road: look for
    circles with a Hough transform (on a half-size copy, for speed) and keep those whose ring is redder than the
    frame (Lab a*). Returns the same dicts as find_signs (without a reading), best 2 only."""
    H, W = bgr.shape[:2]
    sm = cv2.resize(bgr, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
    h, w = sm.shape[:2]
    roi = roi or ROI_LEFT
    y0, y1 = int(h * roi[2]), int(h * roi[3])
    xl, xr = int(w * roi[0]), int(w * roi[1])
    g = cv2.GaussianBlur(cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY), (0, 0), 1.2)
    c = cv2.HoughCircles(g[y0:y1, xl:xr], cv2.HOUGH_GRADIENT, dp=1.2, minDist=20, param1=60, param2=20, minRadius=min_r // 2, maxRadius=max_r // 2)
    if c is None:
        return []
    a = cv2.cvtColor(sm, cv2.COLOR_BGR2LAB)[:, :, 1].astype(np.float32)
    base = float(np.median(a))
    yy, xx = np.ogrid[:h, :w]
    out = []
    for x, y, r in c[0][:60]:
        y = y + y0
        x = x + xl
        xa, xb, ya, yb = max(0, int(x - 2 * r)), min(w, int(x + 2 * r)), max(0, int(y - 2 * r)), min(h, int(y + 2 * r))
        d = np.sqrt((xx[:, xa:xb] - x) ** 2 + (yy[ya:yb] - y) ** 2)
        ring = (d > r * 0.85) & (d < r * 1.12)
        if not ring.any():
            continue
        red = float(a[ya:yb, xa:xb][ring].mean()) - base
        if red < min_ring:
            continue
        X, Y, R = int(x * 2), int(y * 2), int(r * 2)
        pad = int(R * 1.25)
        out.append(dict(x=X, y=Y, r=R, text="", conf=0.0, white=0.0, ok=False, red=round(red, 1),
                        crop=bgr[max(0, Y - pad):min(H, Y + pad), max(0, X - pad):min(W, X + pad)]))
    out.sort(key=lambda d: -d["red"])
    return out[:2]


def find_blue_panels(bgr, roi=ROI_LEFT, max_out=2):
    """Blue rectangular information panels on the left (e.g. 'CHECK YOUR SPEED' with the limit roundel under it).
    Returns dicts like find_signs (x, y = centre of the panel, r = half its larger side) with the crop widened downwards so the
    roundel underneath is included."""
    H, W = bgr.shape[:2]
    x0, x1, y0, y1 = int(W * roi[0]), int(W * roi[1]), int(H * roi[2]), int(H * roi[3])
    sub = bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 0] >= 86) & (hsv[:, :, 0] <= 126) & (hsv[:, :, 1] >= 92) & (hsv[:, :, 2] >= 60) & (hsv[:, :, 2] <= 215)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    g = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 22 or bh < 18 or bw > 420 or bh > 420 or a < 450:
            continue
        if not 0.6 <= bw / float(bh) <= 2.6 or a / float(bw * bh) < 0.62:
            continue
        reg = g[y:y + bh, x:x + bw]
        txt = float((reg > np.median(reg) + 30).mean())              # lighter lettering inside the blue panel
        if not 0.03 <= txt <= 0.5:
            continue
        cx, cy = x + bw / 2.0 + x0, y + bh / 2.0 + y0
        half = max(bw, bh)
        xa, xb = max(0, int(cx - 1.3 * half)), min(W, int(cx + 1.3 * half))
        ya, yb = max(0, int(cy - 0.9 * half)), min(H, int(cy + 1.7 * half))
        out.append(dict(x=int(cx), y=int(cy), r=int(half / 2), kind="panel", text="", conf=0.0, ok=False, red=0.0, score=a * (0.5 + txt),
                        crop=bgr[ya:yb, xa:xb]))
    out.sort(key=lambda d: -d["score"])
    return out[:max_out]


def find_white_signs(bgr, roi=(0.0, 1.0, 0.32, 0.60), max_out=3):
    """White / pale-grey rectangular boards beside the road (distance and information boards, route markers, advisory plates).
    The camera's colour grade turns white into pale teal-grey, so this looks for pale, colourless regions that are clearly
    lighter than the pixels around them, are solid rectangles (up to 4:1) and carry some darker lettering. Sky (huge, no
    lettering) and road paint (thin, low in the picture) are rejected. Returns dicts like find_signs."""
    H, W = bgr.shape[:2]
    x0, x1, y0, y1 = int(W * roi[0]), int(W * roi[1]), int(H * roi[2]), int(H * roi[3])
    sub = bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    g = cv2.GaussianBlur(cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    bg = cv2.blur(g, (121, 121))
    m = ((hsv[:, :, 1] < 80) & (g > 105) & (g.astype(np.int16) - bg.astype(np.int16) > 14)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 16 or bh < 12 or bw > 300 or bh > 130 or a < 260:
            continue
        if not 0.7 <= bw / float(bh) <= 4.4 or a / float(bw * bh) < 0.74:
            continue
        reg = g[y:y + bh, x:x + bw]
        if float(reg.std()) < 9 or float((reg < np.median(reg) - 18).mean()) < 0.03:   # needs some darker lettering
            continue
        ring = g[max(0, y - 8):y + bh + 8, max(0, x - 8):x + bw + 8].astype(np.float32)
        if float(reg.mean()) - float(ring.mean()) < 6:
            continue
        cx, cy = x + bw / 2.0 + x0, y + bh / 2.0 + y0
        half = max(bw, bh)
        xa, xb = max(0, int(cx - 1.4 * half)), min(W, int(cx + 1.4 * half))
        ya, yb = max(0, int(cy - 1.0 * half)), min(H, int(cy + 1.4 * half))
        out.append(dict(x=int(cx), y=int(cy), r=int(half / 2), kind="white", text="", conf=0.0, ok=False, red=0.0, score=float(a),
                        crop=bgr[ya:yb, xa:xb]))
    out.sort(key=lambda d: -d["score"])
    return out[:max_out]


def find_green_signs(bgr, roi=(0.0, 1.0, 0.18, 0.62), max_out=3):
    """Green location / direction boards (white lettering on a flat green rectangle), roadside or on an overhead gantry.
    Leaves and grass are green too, so a candidate must be a SOLID, flat, well-saturated rectangle with a clear lighter lettering
    texture inside; foliage is ragged and fails the fill / flatness test. Returns dicts like find_signs."""
    H, W = bgr.shape[:2]
    x0, x1, y0, y1 = int(W * roi[0]), int(W * roi[1]), int(H * roi[2]), int(H * roi[3])
    sub = bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    g = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    m = ((hsv[:, :, 0] >= 55) & (hsv[:, :, 0] <= 88) & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 45) & (hsv[:, :, 2] <= 190)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 5)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 28 or bh < 14 or bw > 520 or bh > 260 or a < 450:
            continue
        if not 1.0 <= bw / float(bh) <= 6.0 or a / float(bw * bh) < 0.80:
            continue
        peri = cv2.arcLength(c, True)
        if peri <= 0 or 4 * np.pi * a / (peri * peri) < 0.28 and a / float(bw * bh) < 0.9:   # ragged outline = foliage
            continue
        reg = g[y:y + bh, x:x + bw]
        txt = float((reg > np.median(reg) + 28).mean())          # lighter lettering inside the board
        if not 0.02 <= txt <= 0.45:
            continue
        hh = hsv[y:y + bh, x:x + bw, 0]
        if float(hh.std()) > 14:                                   # one flat green, not a mix of leaf shades
            continue
        cx, cy = x + bw / 2.0 + x0, y + bh / 2.0 + y0
        half = max(bw, bh)
        xa, xb = max(0, int(cx - 1.4 * half)), min(W, int(cx + 1.4 * half))
        ya, yb = max(0, int(cy - 1.0 * half)), min(H, int(cy + 1.4 * half))
        out.append(dict(x=int(cx), y=int(cy), r=int(half / 2), kind="green", text="", conf=0.0, ok=False, red=0.0, score=a * (0.5 + txt),
                        crop=bgr[ya:yb, xa:xb], box=(int(x + x0), int(y + y0), int(bw), int(bh))))
    out.sort(key=lambda d: -d["score"])
    return out[:max_out]


def find_road_marks(bgr, roi=(0.24, 0.76, 0.50, 0.84)):
    """Painted speed numerals on the road ahead (white digits, tall in the image because of the perspective). Looks for
    a pair or trio of digit-sized bright shapes side by side. Returns at most one dict (crop of the group) or []."""
    H, W = bgr.shape[:2]
    x0, x1, y0, y1 = int(W * roi[0]), int(W * roi[1]), int(H * roi[2]), int(H * roi[3])
    g = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (45, 45)))
    _, m = cv2.threshold(th, max(30, int(np.percentile(th, 99.3))), 255, cv2.THRESH_BINARY)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _lab, st, _ce = cv2.connectedComponentsWithStats(m, 8)
    comps = []
    for i in range(1, n):
        x, y, bw, bh, a = st[i]
        if 16 <= bw <= 110 and 38 <= bh <= 230 and 1.1 <= bh / float(bw) <= 5.5 and a / float(bw * bh) >= 0.25:
            comps.append((x, y, bw, bh, a))
    best = None
    for i, c in enumerate(comps):
        grp = [c]
        for d in comps[i + 1:] + comps[:i]:
            if d is c:
                continue
            cy_, dy_ = c[1] + c[3] / 2.0, d[1] + d[3] / 2.0
            if abs(cy_ - dy_) <= 0.35 * max(c[3], d[3]) and 0.6 <= d[3] / float(c[3]) <= 1.6 and abs((d[0] + d[2] / 2.0) - (c[0] + c[2] / 2.0)) <= 2.2 * max(c[3], d[3]):
                grp.append(d)
        if len(grp) >= 2:
            sc = len(grp) * sum(q[4] for q in grp)
            if best is None or sc > best[0]:
                best = (sc, grp)
    if best is None:
        return []
    grp = best[1]
    xa, xb = min(q[0] for q in grp), max(q[0] + q[2] for q in grp)
    ya, yb = min(q[1] for q in grp), max(q[1] + q[3] for q in grp)
    pad = int(0.45 * max(xb - xa, yb - ya))
    xa, xb, ya, yb = max(0, xa + x0 - pad), min(W, xb + x0 + pad), max(0, ya + y0 - pad), min(H, yb + y0 + pad)
    return [dict(x=(xa + xb) // 2, y=(ya + yb) // 2, r=max(xb - xa, yb - ya) // 2, kind="road", text="", conf=0.0, ok=False, red=0.0,
                 score=float(best[0]), crop=bgr[ya:yb, xa:xb])]


def find_signs_right(bgr, max_out=2):
    """Round signs on the RIGHT of the view (secondary: a NO ENTRY for an exit ramp, or a resume-speed sign on the other
    carriageway after road works). Uses the circle finder with a red-ring check; returns at most max_out."""
    out = find_signs_hough(bgr, min_r=10, max_r=60, min_ring=6.0, roi=ROI_RIGHT)
    for d in out:
        d["kind"] = "side"
    return out[:max_out]
