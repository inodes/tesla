"""sign_reader.py - reads the number on an Australian speed-limit sign face with a small trained network (numpy only, no OCR process).

The network (an MLP on a 32 x 32 grey picture of the sign's face) was trained by Tools/maintenance/train_sign_reader.py on drawn signs: dark numerals on a white face and the white-on-black
electronic signs over motorways, in several typefaces, at 10 to 60 pixels across, blurred, noisy, compressed and colour-cast like the dashcam's picture. Its weights are lib/sign_reader_model.npz.

    available()                  -> True when the weights are there
    read(face_bgr)               -> (text, conf): "60" and 0-100 (the network's probability); ("", 0.0) for something that is not a limit sign or when the weights are missing
    features(face_bgr)           -> the 1024 numbers the network sees (the trainer uses the same function)
"""
import os

import numpy as np

CLASSES = ["10", "20", "30", "40", "50", "60", "70", "80", "90", "100", "110", "120", "130", "none"]
SIDE = 32
_MODEL = {}


def features(face):
    """A sign face (BGR or grey) -> 1024 floats: grey, 32 x 32, dark numerals on a light face whatever the sign's colours (electronic signs are inverted), zero mean and unit spread."""
    import cv2
    g = face if face.ndim == 2 else cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (SIDE, SIDE), interpolation=cv2.INTER_AREA).astype(np.float32)
    thr, _ = cv2.threshold(g.astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if (g > thr).mean() < 0.5:                  # most of the face is dark: white numerals on a dark panel, turned over
        g = 255.0 - g
    g -= g.mean()
    s = g.std()
    return (g / (s if s > 1e-3 else 1.0)).reshape(-1)


def _load():
    if "w" in _MODEL:
        return _MODEL["w"]
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sign_reader_model.npz")
    try:
        z = np.load(p)
        w = [(z["W%d" % i].astype(np.float32), z["b%d" % i].astype(np.float32)) for i in range(int(z["n"]))]
    except (OSError, KeyError, ValueError):
        w = None
    _MODEL["w"] = w
    return w


def available():
    return _load() is not None


def probs(x):
    w = _load()
    a = x.astype(np.float32).reshape(1, -1)
    for i, (W, b) in enumerate(w):
        a = a @ W + b
        if i < len(w) - 1:
            a = np.maximum(a, 0.0)
    a = a - a.max()
    e = np.exp(a)
    return (e / e.sum())[0]


def read(face):
    if face is None or getattr(face, "size", 0) == 0 or not available():
        return "", 0.0
    p = probs(features(face))
    k = int(p.argmax())
    if CLASSES[k] == "none":
        return "", 0.0
    return CLASSES[k], float(100.0 * p[k])
