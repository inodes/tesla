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

Signs are searched for in the part of the frame above the road, at half size; a blob must look like a sign (a filled, rectangular or diamond
shape of a sensible size with writing in it) so that trees, sky, cars and cones drop out. Text is read with tesseract; for the
white signs a reading is required, since white boxes are everywhere (vehicles, markers).
"""
import os
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
        r = subprocess.run(["tesseract", p, "stdout", "--psm", str(psm), "tsv"], capture_output=True, text=True)
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
        res.append(d)
    return res
