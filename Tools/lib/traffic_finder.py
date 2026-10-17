"""traffic_finder.py - vehicles ahead in our own lane, in one full-size front-camera frame.

  find(bgr, model_path, conf=0.35, min_w=0.015) -> [dict(kind, conf, x, y, w, h, near)]      (pixels of the frame given)

A small object detector (YOLOv8n, COCO classes car / motorcycle / bus / truck) is run with OpenCV's own dnn module on the ONNX file `model_path`: no
extra Python package is needed. A vehicle counts as "in our lane" when the middle of the bottom edge of its box lies inside a lane-shaped wedge
that starts at the vanishing point of the road and widens towards the car (see LANE_*): the camera looks straight ahead along the car, so
our lane is the wedge in the middle of the picture. It is an approximation - a vehicle in the next lane on a bend can be counted, one far
ahead on a bend can be missed - and the page says so. `near` is the box's width as a fraction of the picture's: a rough distance
(a car about 100 m ahead is about 1.5 %, one 20 m ahead about 8 %). Boxes narrower than `min_w` are too far to matter and are dropped.

ensure_model(path, url) fetches the model once (a few MB) when it is not there yet.
"""
import os
import threading
import urllib.request

import cv2
import numpy as np

KINDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}      # COCO ids of the vehicles
LANE_CX = 0.50                      # the middle of our lane, as a fraction of the picture's width
LANE_HORIZON = 0.50                 # the row of the vanishing point (fraction of the height)
LANE_BOTTOM = 0.95                  # the row at which the wedge is LANE_HALF wide ...
LANE_HALF = 0.30                    # ... half of it, as a fraction of the width
LANE_MIN_HALF = 0.035               # but never narrower than this (far ahead the wedge is a point)
_NETS = {}
_LOCK = threading.Lock()


def ensure_model(path, url, timeout=60):
    """The model file at `path`, fetched from `url` the first time. Returns True when it is there."""
    if os.path.isfile(path) and os.path.getsize(path) > 1000000:
        return True
    if not url:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "tesla-app/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(path + ".tmp", "wb") as fh:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                fh.write(b)
        os.replace(path + ".tmp", path)
        return os.path.getsize(path) > 1000000
    except Exception:
        try:
            os.remove(path + ".tmp")
        except OSError:
            pass
        return False


def _net(path):
    if path not in _NETS:
        _NETS[path] = cv2.dnn.readNetFromONNX(path)
    return _NETS[path]


def in_lane(x, y, w, h, W, H):
    """Is the box (x, y, w, h) in our lane? Its bottom-middle point against the wedge of the lane."""
    bx, by = x + w / 2.0, y + h
    if by <= LANE_HORIZON * H:
        return False
    half = max(LANE_MIN_HALF, LANE_HALF * (by / H - LANE_HORIZON) / (LANE_BOTTOM - LANE_HORIZON)) * W
    return abs(bx - LANE_CX * W) <= half


def detect(bgr, model_path, conf=0.35, size=640):
    """Every vehicle in the picture: [(kind, conf, (x, y, w, h))]."""
    h, w = bgr.shape[:2]
    s = size / float(max(h, w))
    nh, nw = int(round(h * s)), int(round(w * s))
    can = np.full((size, size, 3), 114, np.uint8)
    dy, dx = (size - nh) // 2, (size - nw) // 2
    can[dy:dy + nh, dx:dx + nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    blob = cv2.dnn.blobFromImage(can, 1 / 255.0, (size, size), swapRB=True)
    with _LOCK:                                                  # one network, shared by the worker threads
        net = _net(model_path)
        net.setInput(blob)
        out = net.forward()
    out = out[0].T if out.shape[1] < out.shape[2] else out[0]    # (anchors, 4 + classes)
    boxes, scores, kinds = [], [], []
    for r in out:
        sc = r[4:]
        cid = int(np.argmax(sc))
        c = float(sc[cid])
        if c < conf or cid not in KINDS:
            continue
        cx, cy, bw, bh = (float(v) for v in r[:4])
        boxes.append([(cx - bw / 2 - dx) / s, (cy - bh / 2 - dy) / s, bw / s, bh / s])
        scores.append(c)
        kinds.append(KINDS[cid])
    if not boxes:
        return []
    keep = np.array(cv2.dnn.NMSBoxes(boxes, scores, conf, 0.45)).flatten()
    return [(kinds[i], scores[i], tuple(boxes[i])) for i in keep]


def find(bgr, model_path, conf=0.35, min_w=0.015):
    """The vehicles ahead in our lane (boxes narrower than min_w of the picture's width dropped), nearest first."""
    H, W = bgr.shape[:2]
    out = []
    for kind, c, (x, y, w, h) in detect(bgr, model_path, conf):
        if w < min_w * W or not in_lane(x, y, w, h, W, H):
            continue
        x0, y0 = max(0, int(x)), max(0, int(y))
        out.append(dict(kind=kind, conf=c, x=x0, y=y0, w=int(w), h=int(h), near=round(w / W, 3), crop=bgr[y0:y0 + int(h), x0:x0 + int(w)]))
    return sorted(out, key=lambda d: -d["near"])
