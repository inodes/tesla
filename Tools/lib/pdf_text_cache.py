"""pdf_text_cache.py - the text of an invoice PDF, read once and kept (invoice_text_cache.json in the data folder).

Every sync step that looks at the invoices (the scan, the rename, the cost check, each analyse run) used to open and parse every PDF again; with years of invoices that was most of the
Charges sync. The text is now kept by path, size and modified time: a PDF that is new or changed is read, any other is answered from the file. An empty read is not kept (tried again next
time), and what has been read is written every 25 PDFs and at exit, so an interrupted sync does not start over.

    text(pdf_path, reader)   the kept text, else reader(pdf_path) (and keep it)
    forget(pdf_path=None)    drop one PDF (or all): the next look reads it again, to re-check a session by hand
    save()                   write what is kept
"""
import atexit
import json
import os

_CACHE = [None]
_DIRTY = [False]
_NEW = [0]


def _path():
    d = os.environ.get("TESLA_DATA_DIR")
    return os.path.join(os.path.expanduser(d), "invoice_text_cache.json") if d else None


def _key(pdf_path):
    try:
        st = os.stat(pdf_path)
    except OSError:
        return None
    return "%s|%d|%d" % (os.path.realpath(pdf_path), st.st_size, st.st_mtime_ns)


def _cache():
    if _CACHE[0] is None:
        _CACHE[0] = {}
        p = _path()
        try:
            with open(p, encoding="utf-8") as f:
                got = json.load(f)
            if isinstance(got, dict):
                _CACHE[0] = got
        except (OSError, ValueError, TypeError):
            pass
        atexit.register(save)
    return _CACHE[0]


def text(pdf_path, reader):
    key = _key(pdf_path)
    if key is None:
        return reader(pdf_path)
    c = _cache()
    hit = c.get(key)
    if hit:
        return hit
    got = reader(pdf_path)
    if got and got.strip():
        c[key] = got
        _DIRTY[0] = True
        _NEW[0] += 1
        if _NEW[0] % 25 == 0:
            save()
    return got


def forget(pdf_path=None):
    c = _cache()
    if pdf_path is None:
        c.clear()
    else:
        prefix = os.path.realpath(pdf_path) + "|"
        for k in [k for k in c if k.startswith(prefix)]:
            del c[k]
    _DIRTY[0] = True
    save()


def save():
    p = _path()
    if not p or not _DIRTY[0] or _CACHE[0] is None:
        return
    try:
        if os.path.isdir(os.path.dirname(p)):
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_CACHE[0], f, separators=(",", ":"))
            os.replace(tmp, p)
            _DIRTY[0] = False
    except OSError:
        pass
