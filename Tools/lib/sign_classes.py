"""Find road signs of every colour class in a dashcam frame and say what kind each is.

find_all(bgr) -> [dict(cat, shape, x, y, w, h, text, conf, crop)]   (pixels of the frame given)

Classes (Australian signs are told apart by colour first):
  speed        red ring, white face, black number        -> read by sign_finder.find_signs (the number is checked against real limits)
  regulatory   other red-ring or red signs (no entry, give way, stop ...), and white signs with black or red writing
  warning      yellow with black writing (diamond or rectangle)
  roadwork     orange with black writing (temporary road-work signs)
  info         blue with white writing (services, tourist, route information)
  directional  green with white writing (destinations, exits)
  safety       white and blue together (e.g. rest-area, emergency, keep-left style signs)
  school zone  find_school(): the yellow-green SCHOOL ZONE board and the hours on its plate (see the section at the end)

Signs are searched for in the part of the frame above the road, at half size; a blob must look like a sign (a filled, rectangular or diamond
shape of a sensible size with writing in it) so that trees, sky, cars and cones drop out. Text is read with tesseract; for the
white signs a reading is required, since white boxes are everywhere (vehicles, markers).
"""
import difflib
import os
import re
import subprocess
import tempfile

import cv2
import numpy as np

import sign_finder

SCALE = 0.5                          # the search runs at this fraction of the frame
ROI_Y = (0.18, 0.62)                 # rows of the frame searched (fractions of the height): above the road, below the sky line
MIN_PX = 11                          # smallest sign side at the searched size
MAX_FRAC = 0.012                     # biggest sign, as a fraction of the searched area


def _ocr(gray, psm=6):
    """Text in a small grayscale crop via tesseract. Returns (text, mean conf)."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.png")
        cv2.imwrite(p, gray)
        r = subprocess.run(["tesseract", p, "stdout", "--psm", str(psm), "tsv"], capture_output=True, text=True, env=sign_finder.ONE_THREAD)
    words, confs = [], []
    for ln in r.stdout.splitlines()[1:]:
        f = ln.split("\t")
        if len(f) >= 12 and f[11].strip():
            try:
                c = float(f[10])
            except ValueError:
                continue
            if c >= 30:
                words.append(f[11].strip())
                confs.append(c)
    return " ".join(words), (sum(confs) / len(confs) if confs else 0.0)


def _masks(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return {
        "yellow": ((h >= 16) & (h <= 33) & (s >= 120) & (v >= 100)),
        "orange": ((h >= 5) & (h < 18) & (s >= 150) & (v >= 140)),
        "blue": ((h >= 92) & (h <= 126) & (s >= 130) & (v >= 55) & (v <= 215)),
        "green": ((h >= 45) & (h <= 90) & (s >= 100) & (v >= 40) & (v <= 150)),
        "red": (((h <= 8) | (h >= 172)) & (s >= 120) & (v >= 80)),
    }


def _boxes(mask, min_px, max_area):
    m = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if w < min_px or h < min_px or a > max_area or a < 0.4 * w * h:
            continue
        if not 0.4 <= w / float(h) <= 3.2:
            continue
        comp = (lab[y:y + h, x:x + w] == i)
        cnts, _ = cv2.findContours(comp.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        filled = np.zeros(comp.shape, np.uint8)
        cv2.drawContours(filled, [c], -1, 1, -1)
        hull = cv2.convexHull(c)
        solid = cv2.contourArea(c) / max(1.0, cv2.contourArea(hull))
        (_cx, _cy), (rw, rh), _ang = cv2.minAreaRect(c)
        rectfill = cv2.contourArea(c) / max(1.0, rw * rh)
        yield x, y, w, h, comp, filled.astype(bool), solid, rectfill


def _light(gray_crop, thr=150):
    return float((gray_crop >= thr).mean())


TS = 1.0                            # the triangle search runs at full size: its red ring is only a pixel or two thick when small
CK = 7
def _tri_signs(bgr):
    """Give-way signs (a red-bordered triangle point down, white inside), and the roundabout one (dark circling arrows inside). The red ring is looked for by its outline,
    not as a filled blob, so the white inside does not matter; the loose red / low-saturation limits allow for sun on the sign."""
    H0, W0 = bgr.shape[:2]
    y1 = int(H0 * 0.62)
    sm = cv2.resize(bgr[:y1], None, fx=TS, fy=TS, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(sm, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = ((((h <= 10) | (h >= 164)) & (s >= 55) & (v >= 70))).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((CK, CK), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    gray = cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY)
    out = []
    for c in cnts:
        x, y, w, hh = cv2.boundingRect(c)
        if w < 12 * TS / .5 or hh < 10 * TS / .5 or w > 0.12 * sm.shape[1]:
            continue
        if not 1.0 <= w / float(hh) <= 1.5:
            continue
        ap = cv2.approxPolyDP(c, 0.07 * cv2.arcLength(c, True), True)
        if len(ap) != 3:
            continue
        fill = cv2.contourArea(c) / float(w * hh)
        if not 0.42 <= fill <= 0.62:
            continue
        pts = ap.reshape(-1, 2)
        if (pts[:, 1] <= y + 0.25 * hh).sum() != 2:             # two corners along the top, one at the bottom: point down
            continue
        # inside: pale (white face), not red all through (a cone) and not dark
        tri = np.zeros(m.shape, np.uint8)
        cv2.fillPoly(tri, [pts.astype(np.int32)], 255)
        k = max(2, int(0.18 * w))
        inner = cv2.erode(tri, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
        if inner.sum() == 0:
            continue
        ins = inner > 0
        if float((s[ins] < 90).mean()) < 0.6 or float(np.median(v[ins])) < 90:
            continue
        # arrows circling = dark marks spread round the middle of the face, not at its centre
        g = gray[ins].astype(float)
        dark = g < (np.median(g) - 35)
        ys, xs = np.nonzero(ins)
        cx, cy = xs.mean(), ys.mean()
        r = np.hypot(xs - cx, ys - cy)
        ring = False
        if dark.sum() >= 0.05 * ins.sum():
            rr = r[dark]
            ring = float((rr > 0.3 * r.max()).mean()) > 0.7 and float((rr < 0.25 * r.max()).mean()) < 0.15
        out.append(dict(cat="regulatory", col="red", text="Roundabout (give way)" if ring else "Give way", conf=100,
                        x=int(x / TS), y=int(y / TS), w=int(w / TS), h=int(hh / TS), crop=None))
    return out


def _diamond_signs(bgr):
    """Yellow diamond warning signs (speed hump, ...). Looked for by outline: the yellow is taken loosely (sun washes it out), the black symbol is filled in, thin
    necks (a pole, a plate touching the tip) are cut, and what is left must be a square standing on its corner. A dark dome across the middle is a speed hump."""
    H0, W0 = bgr.shape[:2]
    sm = cv2.resize(bgr[:int(H0 * ROI_Y[1])], None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(sm, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = ((h >= 14) & (h <= 36) & (s >= 70) & (v >= 95)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cn, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    f = np.zeros_like(m)
    cv2.drawContours(f, cn, -1, 255, -1)
    f = cv2.morphologyEx(f, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    cn, _ = cv2.findContours(f, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    gray = sm[..., 2]                                   # the red channel: yellow is bright in it even when sun makes it dull, black is not
    out = []
    for c in cn:
        a = cv2.contourArea(c)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
        if a < 120 or min(rw, rh) < 9 or not 0.8 <= rw / float(rh) <= 1.25 or not 28 <= ang % 90 <= 62:
            continue
        if a / max(1.0, cv2.contourArea(cv2.convexHull(c))) < 0.9 or a / (rw * rh) < 0.5:
            continue
        x, y, w, hh = cv2.boundingRect(c)
        msk = np.zeros(f.shape, np.uint8)
        cv2.drawContours(msk, [c], -1, 255, -1)
        ins = msk > 0
        g = gray[ins].astype(float)
        dark = np.zeros(f.shape, bool)
        dark[ins] = g < np.median(g) - 30
        hump = False
        if dark.sum() >= 0.04 * ins.sum():
            ys, xs = np.nonzero(dark)
            hump = (xs.max() - xs.min()) >= 0.5 * w and (ys.max() - ys.min()) <= 0.4 * hh and abs(ys.mean() - (y + hh / 2.0)) <= 0.25 * hh
        out.append(dict(cat="warning", col="yellow", text="Speed hump" if hump else "Warning", conf=100,
                        x=int(x / SCALE), y=int(y / SCALE), w=int(w / SCALE), h=int(hh / SCALE), crop=None))
    return out


def find_all(bgr, speed=True):
    H0, W0 = bgr.shape[:2]
    y0, y1 = int(H0 * ROI_Y[0]), int(H0 * ROI_Y[1])
    roi = bgr[y0:y1]
    small = cv2.resize(roi, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    area = small.shape[0] * small.shape[1]
    out = []
    ms = _masks(hsv)
    for col, cat in (("yellow", "warning"), ("orange", "roadwork"), ("blue", "info"), ("green", "directional")):
        for x, y, w, h, comp, filled, solid, rectfill in _boxes(ms[col], MIN_PX, MAX_FRAC * area):
            if solid < 0.88:
                continue                                         # a sign is a convex shape; foliage and shadows are ragged
            if rectfill < 0.8:
                continue                                         # a sign fills its (turned) bounding rectangle; cones, foliage, shadows do not
            sub = gray[y:y + h, x:x + w]
            fieldmed = float(np.median(sub[comp]))
            holes = filled                                        # the writing is looked for inside the sign's outline
            if col in ("blue", "green"):
                ink = float((holes & (sub > fieldmed + 22)).sum()) / max(1, filled.sum())
            else:
                ink = float((holes & (sub < fieldmed - 20)).sum()) / max(1, filled.sum())
            if not 0.02 <= ink <= 0.6:
                continue
            if col == "green" and (hsv[y:y + h, x:x + w, 1][comp].std() > 75):
                continue                                         # patchy: foliage
            if col == "blue" and y < 0.1 * small.shape[0] and w > 0.1 * small.shape[1]:
                continue                                         # sky
            out.append(dict(col=col, cat=cat, x=x, y=y, w=w, h=h, ink=round(ink, 2), solid=round(solid, 2), rf=round(rectfill, 2)))
    # red signs that are not speed limits (no entry, give way, stop, no stopping ...): a red field with white inside
    for x, y, w, h, comp, filled, solid, rectfill in _boxes(ms["red"], MIN_PX, MAX_FRAC * area):
        if solid < 0.85 or rectfill < 0.7:
            continue
        sub = gray[y:y + h, x:x + w]
        white = float((filled & (sub > 150) & ~comp).sum()) / max(1, filled.sum())
        if 0.08 <= white <= 0.7 and 0.6 <= w / float(h) <= 1.7:
            out.append(dict(col="red", cat="regulatory", x=x, y=y, w=w, h=h, ink=round(white, 2), solid=round(solid, 2), rf=round(rectfill, 2)))
    # white signs with black writing (regulatory / information): a bright, flat, rectangular panel with dark writing in it, and writing that tesseract can read
    brightmask = (hsv[..., 2] >= 150) & (hsv[..., 1] <= 60)
    for x, y, w, h, comp, filled, solid, rectfill in _boxes(brightmask, 14, MAX_FRAC * area):
        if solid < 0.9 or rectfill < 0.85 or not 0.5 <= w / float(h) <= 2.2 or max(w, h) < 16:
            continue
        sub = gray[y:y + h, x:x + w]
        dark = float((filled & (sub < 100)).sum()) / max(1, filled.sum())
        if 0.06 <= dark <= 0.5:
            out.append(dict(col="white", cat="regulatory", x=x, y=y, w=w, h=h, ink=round(dark, 2), solid=round(solid, 2), rf=round(rectfill, 2), need_text=True))
    res = []
    for d in out:
        sx = lambda v: int(v / SCALE)
        X, Y, Wd, Hd = sx(d["x"]), sx(d["y"]) + y0, sx(d["w"]), sx(d["h"])
        pad = max(2, int(0.15 * max(Wd, Hd)))
        crop = bgr[max(0, Y - pad):Y + Hd + pad, max(0, X - pad):X + Wd + pad]
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if g.shape[0] < 110:
            z = 110.0 / g.shape[0]
            g = cv2.resize(g, None, fx=z, fy=z, interpolation=cv2.INTER_CUBIC)
        if d["col"] in ("blue", "green"):
            g = 255 - g                                          # light writing on a dark field -> dark on light, for tesseract
        txt, conf = _ocr(g)
        d.update(x=X, y=Y, w=Wd, h=Hd, text=txt, conf=round(conf), crop=crop, shape="rect")
        if d.pop("need_text", False) and (sum(ch.isalnum() for ch in txt) < 2 or conf < 55):
            continue                                             # white boxes are everywhere: only keep the ones with readable writing
        if d["col"] == "blue" and d["ink"] > 0.5:
            d["cat"] = "safety"                                   # blue and white in about equal parts
        if d["col"] in ("green", "yellow") and is_school([(w, conf) for w in txt.split()]):
            d["cat"], d["text"] = "school", "School zone"          # the fluorescent yellow-green board (find_school reads its hours)
        res.append(d)
    # signs known by their shape: yellow diamonds, and the give-way / roundabout triangle (the sun washes colours out, so these go by outline). A shape that sits on
    # a blob the colour pass already found replaces it.
    def inside(a, b):
        cx, cy = a["x"] + a["w"] / 2.0, a["y"] + a["h"] / 2.0
        return b["x"] <= cx <= b["x"] + b["w"] and b["y"] <= cy <= b["y"] + b["h"]
    extra = []
    for fn in (_diamond_signs, _tri_signs):
        try:
            extra += fn(bgr)
        except Exception:
            pass
    if extra:
        res = [r for r in res if not any(inside(r, e) for e in extra)]
        for e in extra:
            pad = max(2, int(0.15 * max(e["w"], e["h"])))
            e["crop"] = bgr[max(0, e["y"] - pad):e["y"] + e["h"] + pad, max(0, e["x"] - pad):e["x"] + e["w"] + pad]
            e.update(shape="shape", ink=0, solid=1, rf=1, sym=True)
            res.append(e)
    return res


# ---- school zone signs: the board, and the hours on the plate under it -----------------------------------------------------------------------------------------
# An NSW school zone sign is a fluorescent yellow-green board reading SCHOOL ZONE, with a plate under it giving the hours the 40 limit applies ("8-9:30 / 2:30-4") next to
# the red-ringed 40. The board is found by colour and shape and must read as SCHOOL / ZONE (fuzzy: a few letters wrong is fine; a yellow diamond or a leaf is not). The row
# under it is then read for the hours: the red ring is blanked, the writing enlarged and read as digits, and what was read becomes 24-hour ranges (parse_times). Hours are
# given only when they read as real ones (one to three ranges, 20 minutes to 4 hours long): a plate too small or blurred to read gives no hours, never guessed ones.
SZ_ROI_Y = (0.10, 0.75)              # rows of the frame searched (fractions of the height): above the road, below the sky
SZ_MIN_W = 24                        # narrowest board (pixels of the frame) worth reading
SZ_OCR_H = 110                       # the board is enlarged to about this height for reading
SZ_PLATE_H = 90                      # the plate's writing is enlarged to about this height
SZ_MIN_BLOBS = 3                     # letter-sized dark shapes a board must show before it is read: a board has two rows of letters, leaves and paint have none or one


def _tsv(gray, psm, whitelist=None):
    """[(word, confidence)] tesseract read in a grayscale image."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.png")
        cv2.imwrite(p, gray)
        cmd = ["tesseract", p, "stdout", "--psm", str(psm)]
        if whitelist:
            cmd += ["-c", "tessedit_char_whitelist=" + whitelist]
        r = subprocess.run(cmd + ["tsv"], capture_output=True, text=True, stdin=subprocess.DEVNULL, env=sign_finder.ONE_THREAD)
    out = []
    for ln in r.stdout.splitlines()[1:]:
        f = ln.split("\t")
        if len(f) >= 12 and f[11].strip():
            try:
                c = float(f[10])
            except ValueError:
                continue
            if c >= 0:
                out.append((f[11].strip(), c))
    return out


def is_school(words):
    """True when the words read off a board say SCHOOL (or ZONE): letters may be wrong, so each is compared by similarity."""
    hit = 0
    for w, _c in words:
        w = re.sub(r"[^A-Za-z]", "", w).upper()
        if len(w) >= 3 and (difflib.SequenceMatcher(None, w, "SCHOOL").ratio() >= 0.66 or difflib.SequenceMatcher(None, w, "ZONE").ratio() >= 0.75):
            hit += 1
    return hit >= 1


_RANGE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*-\s*(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", re.I)


def parse_times(text):
    """Hours on a school zone plate as sorted 24-hour ranges ["08:00-09:30", "14:30-16:00"] ([] when what was read is not hours).
    "8-9:30 2:30-4", "8.00 - 9.30AM 2.30 - 4.00PM" and "8:00am-9:30am / 2:30pm-4:00pm" all read the same. A range with no am/pm takes the one of the other end; with
    neither, the first range is the morning one (when it starts at 6 or later) and the next is the afternoon."""
    t = re.sub(r"[‐-―−_~]+", "-", text or "")
    out = []
    for m in _RANGE.finditer(t):
        h0, m0, a0, h1, m1, a1 = m.groups()
        h0, h1, m0, m1 = int(h0), int(h1), int(m0 or 0), int(m1 or 0)
        if not (0 < h0 <= 12 and 0 < h1 <= 12 and m0 < 60 and m1 < 60 and m0 % 5 == 0 and m1 % 5 == 0):
            continue
        a0, a1 = (a0 or "").lower()[:1], (a1 or "").lower()[:1]
        if not a0 and not a1:
            a0 = a1 = "a" if (not out and (h0 >= 6 or h0 == 12) and h1 <= 12 and (h1 > h0 or h1 < 7 or h0 == 12) and h0 < 12) else "p"
        elif not a0:
            a0 = "a" if (a1 == "p" and h0 > h1) else a1
        elif not a1:
            a1 = a0
        s, e = (h0 % 12 + (12 if a0 == "p" else 0)) * 60 + m0, (h1 % 12 + (12 if a1 == "p" else 0)) * 60 + m1
        if not 20 <= e - s <= 240:
            continue
        r = "%02d:%02d-%02d:%02d" % (s // 60, s % 60, e // 60, e % 60)
        if r not in out:
            out.append(r)
    return sorted(out)[:3]


def _sz_boards(bgr):
    """[(x, y, w, h)] of fluorescent yellow-green rectangles in the part of the frame that signs hang in."""
    H, W = bgr.shape[:2]
    y0, y1 = int(H * SZ_ROI_Y[0]), int(H * SZ_ROI_Y[1])
    hsv = cv2.cvtColor(bgr[y0:y1], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = (((h >= 28) & (h <= 62) & (s >= 100) & (v >= 140)).astype(np.uint8)) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, hh, a = st[i]
        if w < SZ_MIN_W * 0.6 or hh < 6 or a < 0.55 * w * hh or not 1.2 <= w / float(hh) <= 4.5 or w > 0.3 * W:
            continue
        out.append((int(x), int(y + y0), int(w), int(hh), int(a)))
    out.sort(key=lambda b: -b[4])
    return [b[:4] for b in out[:4]]


def _letter_blobs(bgr, box):
    """How many letter-sized dark shapes lie on a board (SCHOOL / ZONE are eight or more; leaves, a yellow line or a distant blur give none to two). A look at the pixels
    that costs a millisecond, where reading the board costs two runs of tesseract."""
    x, y, w, h = box
    g = _enlarge(cv2.cvtColor(bgr[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY), 60)
    ink = (cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1] == 0).astype(np.uint8)
    n, _l, st, _c = cv2.connectedComponentsWithStats(ink, 8)
    gh = g.shape[0]
    return sum(1 for i in range(1, n) if 0.12 * gh <= st[i][3] <= 0.6 * gh and st[i][2] <= 0.5 * gh + 5 and st[i][4] >= 8)


def _enlarge(gray, height):
    z = max(1.0, float(height) / max(1, gray.shape[0]))
    return cv2.resize(gray, None, fx=z, fy=z, interpolation=cv2.INTER_CUBIC) if z > 1.0 else gray


def read_plate(bgr, box):
    """(["08:00-09:30", ...], confidence) for the plate under a school zone board, or ([], 0). The red ring of the limit beside the plate is blanked first."""
    x, y, w, h = box
    H, W = bgr.shape[:2]
    xa, xb = max(0, int(x - 0.15 * w)), min(W, int(x + 1.15 * w))
    ya, yb = min(H - 1, y + h), min(H, int(y + h + 1.25 * h))
    row = bgr[ya:yb, xa:xb].copy()
    if row.shape[0] < 8 or row.shape[1] < 16:
        return [], 0.0
    hsv = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
    red = ((((hsv[..., 0] <= 8) | (hsv[..., 0] >= 170)) & (hsv[..., 1] >= 110) & (hsv[..., 2] >= 70)).astype(np.uint8)) * 255
    n, _l, st, _c = cv2.connectedComponentsWithStats(cv2.dilate(red, np.ones((5, 5), np.uint8)), 8)
    for i in range(1, n):                                           # the limit's ring: blank its square so the 40 is not read as an hour
        rx, ry, rw, rh, ra = st[i]
        if ra > 0.15 * rw * rh and rw >= 6 and rh >= 6:
            row[max(0, ry - 2):ry + rh + 2, max(0, rx - 2):rx + rw + 2] = 255
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(_enlarge(cv2.cvtColor(row, cv2.COLOR_BGR2GRAY), SZ_PLATE_H))
    best, bconf = [], 0.0
    for thr in ("otsu", "raw"):
        im = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1] if thr == "otsu" else g
        if float((im < 128).mean()) > 0.5:                          # a dark plate: the writing is light, so flip
            im = 255 - im
        im = cv2.copyMakeBorder(im, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        for psm in (6, 4, 11):
            words = _tsv(im, psm, "0123456789:.-AMPamp ")
            tm = parse_times(" ".join(wd for wd, _c in words))
            if tm:
                conf = sum(c for _w, c in words) / max(1, len(words))
                if len(tm) > len(best) or (len(tm) == len(best) and conf > bconf):
                    best, bconf = tm, conf
        if len(best) >= 2:
            break
    return best, round(bconf)


def find_school(bgr, read_times=True):
    """School zone signs in a frame -> [dict(x, y, w, h, r, conf, text, times, tconf, side, crop)] (pixels of the frame given). `times` is the 24-hour ranges read from the
    plate, empty when it could not be read; `crop` is the board with the plate and limit under it."""
    H, W = bgr.shape[:2]
    out = []
    for (x, y, w, h) in _sz_boards(bgr):
        if _letter_blobs(bgr, (x, y, w, h)) < SZ_MIN_BLOBS:
            continue
        pad = max(2, int(0.2 * max(w, h)))
        g = _enlarge(cv2.cvtColor(bgr[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad], cv2.COLOR_BGR2GRAY), SZ_OCR_H)
        words = []
        for im in (255 - g, g):                                        # dark writing on the green, or (sun) the other way round
            words = _tsv(cv2.copyMakeBorder(im, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=int(im[0, 0])), 6)
            if is_school(words):
                break
        if not is_school(words):
            continue
        letters = [c for wd, c in words if re.search(r"[A-Za-z]", wd)]
        times, tconf = read_plate(bgr, (x, y, w, h)) if read_times and w >= 40 else ([], 0)
        cx = x + w / 2.0
        out.append(dict(x=int(cx), y=int(y + h / 2.0), w=w, h=h, r=int(w / 2), conf=round(sum(letters) / max(1, len(letters))), text="School zone", times=times, tconf=tconf,
                        side="L" if cx < W / 2.0 else "R", crop=bgr[max(0, y - pad):min(H, int(y + 2.6 * h + pad)), max(0, int(x - 0.3 * w)):min(W, int(x + 1.3 * w))]))
    return out
