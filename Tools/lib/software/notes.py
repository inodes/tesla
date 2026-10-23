"""notes.py - what a release note is called (its key) and what it says (its hash).

    key("FSD (Supervised) v14.3.12 Down Under")  -> "fsd (supervised) down under"   the same note whatever FSD version it names
    text_hash(description, subtitle)             -> 16 hex characters of the text with spacing and bullets normalised
    fsd_version(title, description)              -> "14.3.12" when the note names one, else None
    diff(old, new)                               -> [{op: same|add|del, text}]: what the new text added and what it dropped, word by word

The key says WHICH note it is; the hash says WHAT IT SAYS. Same key and same hash in a later release = the note is listed again.
Same key, different hash = the note was revised (FSD 14.3.5 -> 14.3.12; Speed Profiles gaining Mad Max). A different key is a different note.
"""
import difflib
import hashlib
import re
import unicodedata

_VER = re.compile(r"\bv?\d+(?:\.\d+)+\b", re.I)
_FSD = re.compile(r"\bv?(\d{1,2}\.\d+(?:\.\d+){0,2})\b")


def _flat(s):
    return " ".join(unicodedata.normalize("NFKC", s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').split())


def key(title):
    t = _VER.sub(" ", _flat(title).lower())
    t = re.sub(r"[^a-z0-9()&+\s-]", "", t)
    return " ".join(t.split())


def text_hash(description, subtitle=""):
    body = _flat(subtitle) + "\n" + _flat(description) if _flat(subtitle) else _flat(description)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def fsd_version(title, description=""):
    for s in (title or "", ):
        if re.search(r"\b(fsd|full self-driving)\b", s, re.I):
            m = _FSD.search(s)
            if m:
                return m.group(1)
    return None


def diff(old, new):
    """How a note's text changed: word by word, keeping the spacing and line breaks. -> [{op, text}] with op `same`, `add` (in the new text only) or `del` (in the
    old text only, shown struck through). Joining the `same` and `add` parts gives the new text; the `same` and `del` parts give the old."""
    a, b = re.findall(r"\s+|[^\s]+", old or ""), re.findall(r"\s+|[^\s]+", new or "")
    out = []

    def put(op, toks):
        t = "".join(toks)
        if not t:
            return
        if out and out[-1]["op"] == op:
            out[-1]["text"] += t
        else:
            out.append({"op": op, "text": t})
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.ratio() < 0.4:                                    # a different text altogether: the old one out, the new one in, not a scatter of words
        put("del", a)
        put("add", b)
        return out
    ops = sm.get_opcodes()
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        tiny = tag == "equal" and 0 < n < len(ops) - 1 and len("".join(b[j1:j2]).strip()) <= 3      # a stray space or "- " between two changes belongs to both
        if tag == "equal" and not tiny:
            put("same", b[j1:j2])
        else:
            put("del", a[i1:i2])
            put("add", b[j1:j2])
    # the removed words come before the new ones in each changed stretch
    res, i = [], 0
    while i < len(out):
        if out[i]["op"] in ("add", "del"):
            j = i
            while j < len(out) and out[j]["op"] in ("add", "del"):
                j += 1
            run = out[i:j]
            res += [{"op": "del", "text": "".join(x["text"] for x in run if x["op"] == "del")}, {"op": "add", "text": "".join(x["text"] for x in run if x["op"] == "add")}]
            i = j
        else:
            res.append(out[i])
            i += 1
    return [x for x in res if x["text"]]
