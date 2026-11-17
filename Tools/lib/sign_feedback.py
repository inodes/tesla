#!/usr/bin/env python3
"""
sign_feedback.py - the person's answers about the signs read from the video ("not a speed sign", "wrong speed", "school zone / not a school zone").

An answer is kept in <data>/sign_training/feedback.json, keyed like the training overrides: "<clip stamp>|<second the sign was first seen>|<number as read>".
    {"reason": "none" | "speed" | "school" | "notschool", "speed": 60 (reason speed only), "text": "40" (as read), "s0": 12.0, "s1": 14.0, "at": "2026-11-18T01:02:03Z"}
It does three things:
  - the signs the page gets (app_review.Review.minute_signs) are corrected by it: "none" takes the sign out (it is listed apart, with an Undo), "speed" changes the number,
    "school" / "notschool" set `zone` (True / False) on the sign: is it a school zone's limit (the board's own hours stay as read);
  - every answer that says what the sign really is also goes into <data>/sign_training/overrides.json ("none" or the number), which sign_check_drive.py turns into training faces;
  - clearing an answer removes it from both.
"""
import json
import os
import threading
import time

REASONS = ("none", "speed", "school", "notschool")
_LOCK = threading.RLock()


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, path)


def key(stamp, s0, text):
    """The second is always written as a float ("5.0"), whether the page sent 5 or 5.0 and whether the stored reading holds an int or a float."""
    return "%s|%s|%s" % (stamp, float(s0), text)


class Feedback:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "sign_training")
        self.f_fb = os.path.join(self.dir, "feedback.json")
        self.f_over = os.path.join(self.dir, "overrides.json")
        self._memo = None

    def all(self):
        """{key: answer}, read again only when the file changed."""
        try:
            mt = os.stat(self.f_fb).st_mtime_ns
        except OSError:
            mt = None
        if self._memo is None or self._memo[0] != mt:
            self._memo = (mt, _read(self.f_fb))
        return self._memo[1]

    def set(self, stamp, s0, text, reason, speed=None, s1=None):
        """Keep an answer. Returns (answer, error)."""
        if reason not in REASONS:
            return None, "Unknown reason."
        if reason == "speed":
            try:
                speed = int(speed)
            except (TypeError, ValueError):
                return None, "Say which speed the sign shows."
            if not 5 <= speed <= 130 or speed % 5:
                return None, "A speed limit is a multiple of 5 between 5 and 130."
        else:
            speed = None
        k = key(stamp, s0, text)
        a = dict(reason=reason, text=str(text), s0=s0, s1=s0 if s1 is None else s1, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if speed is not None:
            a["speed"] = speed
        with _LOCK:
            fb = _read(self.f_fb)
            fb[k] = a
            _write(self.f_fb, fb)
            over = _read(self.f_over)
            if reason == "none":
                over[k] = "none"
            elif reason == "speed":
                over[k] = str(speed)
            else:
                over.pop(k, None)
            _write(self.f_over, over)
        self._memo = None
        return a, None

    def clear(self, stamp, s0, text):
        k = key(stamp, s0, text)
        with _LOCK:
            fb = _read(self.f_fb)
            if fb.pop(k, None) is not None:
                _write(self.f_fb, fb)
            over = _read(self.f_over)
            if over.pop(k, None) is not None:
                _write(self.f_over, over)
        self._memo = None

    def apply(self, stamp, groups, dismissed=False):
        """The signs of one minute with the answers applied. dismissed=False: the signs that are left (an answered one carries `fb` = {r, was, v?}); dismissed=True: only
        the signs answered "not a speed sign" (each with `fb` = {r: "none", was}), for the list that lets the person take an answer back."""
        fb = self.all()
        out = []
        for g in groups:
            a = fb.get(key(stamp, g.get("s0"), g.get("text")))
            if not a:
                if not dismissed:
                    out.append(g)
                continue
            r = a.get("reason")
            g = dict(g, fb=dict(r=r, was=g.get("text")))
            if r == "none":
                if dismissed:
                    out.append(g)
                continue
            if dismissed:
                continue
            if r == "speed":
                g["text"] = str(a["speed"])
                g["conf"] = 100
                g["fb"]["v"] = a["speed"]
            elif r in ("school", "notschool"):
                g["zone"] = r == "school"
            out.append(g)
        return out
