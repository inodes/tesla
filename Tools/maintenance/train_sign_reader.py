#!/usr/bin/env python3
"""train_sign_reader.py - trains the small network that reads speed-limit signs (lib/sign_reader.py) on DRAWN Australian signs. Run it anywhere with numpy, opencv, pillow and scikit-learn
(not on the user's Mac: the app itself needs only numpy):

    python3 Tools/maintenance/train_sign_reader.py --out Tools/lib/sign_reader_model.npz [--n 60000] [--seed 1]

Signs are drawn at 200 px across: a red ring, a white face with dark numerals (or the electronic signs over a motorway: white numerals on a black panel), the numerals in one of several bold
sans typefaces and as wide as the sign lets them be (three digits are narrower than two). They are then made to look like a dashcam still: turned and tilted, off-centre in the crop, shrunk to
10 - 60 px, blurred, noisy, JPEG-compressed, colour-cast. A "none" class (blank faces, single digits, letters, plain texture) is what is left for something that is not a limit sign.
"""
import argparse
import os
import random
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sign_reader as SR  # noqa: E402

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf", "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
         "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf", "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf"]
FONTS = [f for f in FONTS if os.path.exists(f)]
D = 200                                                        # the drawn sign, across
_FC = {}


def font(path, size):
    k = (path, size)
    if k not in _FC:
        _FC[k] = ImageFont.truetype(path, size)
    return _FC[k]


def numerals(text, rng, ink, w_max, h_frac):
    """The text as an RGBA picture, as tall as h_frac of the sign and no wider than w_max."""
    path = rng.choice(FONTS)
    f = font(path, 100)
    box = f.getbbox(text)
    tw, th = box[2] - box[0], box[3] - box[1]
    img = Image.new("L", (tw + 8, th + 8), 0)
    ImageDraw.Draw(img).text((4 - box[0], 4 - box[1]), text, font=f, fill=255)
    h = int(D * h_frac)
    w = min(int((tw + 8) * h / float(th + 8)), w_max)
    return np.array(img.resize((max(4, w), max(4, h)), Image.BILINEAR), dtype=np.float32) / 255.0


def draw_sign(text, rng):
    """A drawn sign (D x D x 3 BGR float)."""
    led = rng.random() < 0.22                                  # the electronic signs: white numerals on black
    bg = rng.randint(0, 45) if led else rng.randint(205, 255)
    face = np.full((D, D, 3), rng.randint(120, 200), np.float32)           # what is behind the sign: sky, trees, a board
    face += np.random.RandomState(rng.randint(0, 10 ** 6)).randn(D, D, 3).astype(np.float32) * 6
    c = D // 2
    ring = rng.choice([(40, 30, 200), (35, 35, 190), (60, 40, 215), (30, 25, 170)])           # BGR reds
    cv2.circle(face, (c, c), int(D * 0.48), ring, -1, cv2.LINE_AA)
    cv2.circle(face, (c, c), int(D * 0.48 * rng.uniform(0.80, 0.86)), (bg, bg, bg), -1, cv2.LINE_AA)
    if text:
        ink_v = rng.randint(215, 255) if led else rng.randint(0, 50)
        n = len(text)
        h_frac = rng.uniform(0.40, 0.52)
        num = numerals(text, rng, ink_v, int(D * (0.50 if n == 3 else 0.54)), h_frac)
        nh, nw = num.shape
        x0, y0 = c - nw // 2 + rng.randint(-3, 3), c - nh // 2 + rng.randint(-3, 3)
        for ch in range(3):
            reg = face[y0:y0 + nh, x0:x0 + nw, ch]
            reg[:] = reg * (1 - num[:reg.shape[0], :reg.shape[1]]) + ink_v * num[:reg.shape[0], :reg.shape[1]]
    return face


def dashcam(img, rng):
    """Make a drawn sign look like a dashcam still of a sign at a distance."""
    ang, sh = rng.uniform(-9, 9), rng.uniform(-0.12, 0.12)
    sc = rng.uniform(0.92, 1.12)
    M = cv2.getRotationMatrix2D((D / 2, D / 2), ang, sc)
    M[0, 1] += sh
    pts = np.float32([[0, 0], [D, 0], [D, D], [0, D]])
    jig = np.float32([[rng.uniform(-9, 9), rng.uniform(-9, 9)] for _ in range(4)])
    P = cv2.getPerspectiveTransform(pts, pts + jig)
    img = cv2.warpAffine(img, M, (D, D), borderMode=cv2.BORDER_REPLICATE)
    img = cv2.warpPerspective(img, P, (D, D), borderMode=cv2.BORDER_REPLICATE)
    # the crop the finder makes: the square inside the ring, its centre a little off
    half = D * 0.48 * 0.74 * rng.uniform(0.88, 1.15)
    cx, cy = D / 2 + rng.uniform(-0.07, 0.07) * D, D / 2 + rng.uniform(-0.07, 0.07) * D
    x0, x1, y0, y1 = int(max(0, cx - half)), int(min(D, cx + half)), int(max(0, cy - half)), int(min(D, cy + half))
    crop = img[y0:y1, x0:x1]
    side = rng.choice([rng.randint(9, 20), rng.randint(14, 36), rng.randint(24, 64)])
    crop = cv2.resize(crop, (side, side), interpolation=cv2.INTER_AREA)
    if rng.random() < 0.75:
        k = rng.uniform(0.3, 1.5)
        crop = cv2.GaussianBlur(crop, (0, 0), k)
    if rng.random() < 0.25:                                    # motion blur
        n = rng.choice([3, 5])
        ker = np.zeros((n, n), np.float32)
        ker[n // 2, :] = 1.0 / n
        M2 = cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), rng.uniform(0, 180), 1)
        crop = cv2.filter2D(crop, -1, cv2.warpAffine(ker, M2, (n, n)))
    gamma, gain = rng.uniform(0.7, 1.5), rng.uniform(0.6, 1.2)
    crop = 255.0 * np.power(np.clip(crop / 255.0, 0, 1), gamma) * gain
    cast = np.array([rng.uniform(0.85, 1.05), rng.uniform(0.95, 1.15), rng.uniform(0.85, 1.05)], np.float32)       # the dashcam's green cast
    crop = crop * cast + np.random.randn(*crop.shape).astype(np.float32) * rng.uniform(0, 9)
    crop = np.clip(crop, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(25, 90)])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def negative(rng):
    k = rng.random()
    side = rng.randint(10, 60)
    if k < 0.25:                                               # a blank face
        return dashcam(draw_sign("", rng), rng)
    if k < 0.5:                                                # a single digit or a letter that is not a limit
        return dashcam(draw_sign(rng.choice(list("0123456789ABCDEFGHKMPRSTWXY") + ["15", "25", "35", "45", "55", "65", "75", "85", "95", "1", "00", "200", "140", "5", "88", "33", "99"]), rng), rng)
    rs = np.random.RandomState(rng.randint(0, 10 ** 6))
    base = rs.uniform(30, 220, 3).astype(np.float32)
    n = cv2.resize(rs.randn(rng.randint(2, 8), rng.randint(2, 8), 3).astype(np.float32) * rng.uniform(5, 60), (side, side), interpolation=cv2.INTER_CUBIC)
    img = np.clip(base + n + rs.randn(side, side, 3).astype(np.float32) * rng.uniform(0, 8), 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (0, 0), rng.uniform(0, 1.2))


def sample(seed):
    rng = random.Random(seed)
    np.random.seed(seed % (2 ** 31))
    if rng.random() < 0.09:
        return SR.features(negative(rng)), len(SR.CLASSES) - 1
    k = rng.randrange(len(SR.CLASSES) - 1)
    img = dashcam(draw_sign(SR.CLASSES[k], rng), rng)
    return SR.features(img), k


def make(n, seed0):
    from multiprocessing import Pool
    with Pool(max(1, os.cpu_count() or 1)) as p:
        out = p.map(sample, range(seed0, seed0 + n), chunksize=500)
    return np.stack([o[0] for o in out]), np.array([o[1] for o in out])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sign_reader_model.npz")
    ap.add_argument("--n", type=int, default=60000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--real", default="", help="npz of real sign faces (g: 32 x 32 grey, t: the text tesseract read, d: day index, c: its confidence) to train on alongside the drawn ones")
    ap.add_argument("--sample-dir", default="", help="write a few drawn samples here as pictures, to look at")
    a = ap.parse_args()
    from sklearn.neural_network import MLPClassifier
    X, y = make(a.n, a.seed * 1000003)
    Xv, yv = make(6000, a.seed * 1000003 + a.n + 7)
    print("drawn", X.shape, "held out", Xv.shape, flush=True)
    real_test = None
    if a.real:
        from sign_reader import CLASSES, features
        z = np.load(a.real)
        keep = [i for i, (t, c) in enumerate(zip(z["t"], z["c"])) if c >= 75 and (t in CLASSES or not t.isdigit())]
        lab = np.array([CLASSES.index(z["t"][i]) if z["t"][i] in CLASSES else CLASSES.index("none") for i in keep])
        F = np.array([features(z["g"][i]) for i in keep], np.float32)
        hold = (z["d"][keep] % 4) == 0
        print("real faces", len(keep), "held out days", int(hold.sum()), flush=True)
        real_test = (F[hold], lab[hold])
        Fr, lr = F[~hold], lab[~hold]
        X = np.vstack([X] + [Fr] * 4)
        y = np.concatenate([y] + [lr] * 4)
    clf = MLPClassifier(hidden_layer_sizes=(256, 128), activation="relu", solver="adam", alpha=1e-4, batch_size=256, max_iter=40, early_stopping=True, n_iter_no_change=5, random_state=a.seed, verbose=False)
    clf.fit(X, y)
    acc = (clf.predict(Xv) == yv).mean()
    print("held-out accuracy on drawn signs: %.4f" % acc, "iterations", clf.n_iter_, flush=True)
    if real_test is not None:
        print("held-out accuracy on real signs (days not trained on): %.4f" % (clf.predict(real_test[0]) == real_test[1]).mean(), flush=True)
    np.savez_compressed(a.out, n=np.array(len(clf.coefs_)), **{("W%d" % i): w.astype(np.float16 if False else np.float32) for i, w in enumerate(clf.coefs_)}, **{("b%d" % i): b.astype(np.float32) for i, b in enumerate(clf.intercepts_)})
    print("wrote", a.out, os.path.getsize(a.out), "bytes")


if __name__ == "__main__":
    main()
