"""oslog.py - the app's log in Apple's unified logging system (os_log / OSLog), the one Console.app and `log` read:
https://developer.apple.com/documentation/os/logging

    L = oslog.get("perf")           one logger per category (subsystem net.inodes.tesellate, the app's bundle id)
    L.info("server bound port=8765")      also L.debug / L.notice / L.error / L.fault
    oslog.install()                 at start: unhandled exceptions (main thread and other threads) go to the log as faults, a watchdog notes every stall

Read it in Console.app (search "subsystem:net.inodes.tesellate") or in Terminal:
    log stream --level debug --predicate 'subsystem == "net.inodes.tesellate"'
    log show --last 1h --info --debug --predicate 'subsystem == "net.inodes.tesellate" AND category == "stall"'

Python has no binding for os_log, whose C interface is macros: this calls the function under them (`_os_log_impl`) through ctypes with one public string argument, or
uses the `pyoslog` package when it is installed. Messages are marked public, so Console shows them (the default would show "<private>"). Where neither works (not a Mac,
the call missing) the lines go to syslog, and then to nothing: logging never raises and never slows what it logs by more than a call.

`backend()` says which is used. `test_line()` writes one line and reads it back with `log show` (the debug page of the app uses it to prove the log arrives).
"""
import ctypes
import ctypes.util
import os
import subprocess
import sys
import threading
import time

SUBSYSTEM = "net.inodes.tesellate"
# os_log_type_t
DEFAULT, INFO, DEBUG, ERROR, FAULT = 0x00, 0x01, 0x02, 0x10, 0x11
_NAMES = {DEFAULT: "notice", INFO: "info", DEBUG: "debug", ERROR: "error", FAULT: "fault"}
_STATE = {"backend": None, "libc": None, "py": None}
_LOGS = {}
_LOCK = threading.Lock()
_FMT = b"%{public}s"


def _find_format(libsys):
    """(header address, address of "%{public}s") inside a system library's __TEXT. The log decodes a message by reading its format string out of the image the call names (by that
    image's UUID and the string's offset in it): a format string in Python's memory is not in any image and shows as "<compose failure>". A system library holds this exact string, so
    the call names that library and that address. (None, None) when no loaded image holds it."""
    import struct
    libsys._dyld_image_count.restype = ctypes.c_uint32
    libsys._dyld_get_image_header.restype = ctypes.c_void_p
    libsys._dyld_get_image_header.argtypes = [ctypes.c_uint32]
    libsys._dyld_get_image_name.restype = ctypes.c_char_p
    libsys._dyld_get_image_name.argtypes = [ctypes.c_uint32]
    for i in range(libsys._dyld_image_count()):
        name = libsys._dyld_get_image_name(i) or b""
        if not (name.startswith(b"/usr/lib/") or name.startswith(b"/System/")):
            continue
        hdr = libsys._dyld_get_image_header(i)
        if not hdr:
            continue
        magic, _cpu, _sub, _ft, ncmds, _sz, _fl, _res = struct.unpack("<IiiIIIII", ctypes.string_at(hdr, 32))
        if magic != 0xFEEDFACF:
            continue
        off = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack("<II", ctypes.string_at(hdr + off, 8))
            if cmd == 0x19:                                           # LC_SEGMENT_64
                segname, _vmaddr, vmsize = struct.unpack("<16sQQ", ctypes.string_at(hdr + off + 8, 32))
                if segname.rstrip(b"\0") == b"__TEXT":
                    at = ctypes.string_at(hdr, vmsize).find(_FMT + b"\0")
                    if at >= 0:
                        return hdr, hdr + at
                    break
            off += size
    return None, None


def _setup_ctypes():
    """The os_log functions through ctypes: (libc, dso, format address). `dso` is the ADDRESS of the Mach-O header of the image holding the format string."""
    libc = ctypes.CDLL(ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib")
    libc.os_log_create.restype = ctypes.c_void_p
    libc.os_log_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    libc._os_log_impl.restype = None
    libc._os_log_impl.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint8, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    dso, fmt = _find_format(libc)
    if not dso:
        raise OSError("no image holds the format string")
    return libc, (dso, fmt)


def _emit(libc, dso, h, level, text):
    """One message at `level`. `dso` is (header address, format address), as _setup_ctypes gave."""
    data = text.encode("utf-8", "replace")
    s = ctypes.create_string_buffer(data)
    buf = bytearray(b"\x02\x01\x22\x08") + ctypes.addressof(s).to_bytes(8, "little")        # summary: has a non-scalar; 1 argument: public string, 8 bytes: its address
    raw = (ctypes.c_char * len(buf)).from_buffer(buf)
    libc._os_log_impl(ctypes.c_void_p(dso[0]), h, level, ctypes.c_void_p(dso[1]), ctypes.cast(raw, ctypes.c_char_p), len(buf))


def _child():
    """Run in a child process by _ctypes_safe: one message through the ctypes path. A wrong buffer or header kills the process that makes the call, so the app never makes it
    before a child has: a child that dies is an answer, the app dying is not."""
    libc, dso = _setup_ctypes()
    h = libc.os_log_create(SUBSYSTEM.encode(), b"selftest")
    _emit(libc, dso, h, DEFAULT, "ctypes check in a child process")


def _ctypes_safe():
    """True when a child process can write a message through the ctypes path and exit normally (it is asked once per start, 0.1 s)."""
    if os.environ.get("TESELLATE_NO_OSLOG"):
        return False
    try:
        r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import oslog; oslog._child()" % os.path.dirname(os.path.abspath(__file__))],
                           capture_output=True, timeout=8, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _load():
    """Chooses the backend once: "pyoslog", "ctypes" (only after a child process has used it safely), "syslog" or "none"."""
    with _LOCK:
        if _STATE["backend"]:
            return _STATE["backend"]
        be = "none"
        if sys.platform == "darwin":
            try:
                import pyoslog                     # optional: a compiled binding, when the person has installed it
                _STATE["py"] = pyoslog
                be = "pyoslog"
            except Exception:
                try:
                    if _ctypes_safe():
                        _STATE["libc"], _STATE["dso"] = _setup_ctypes()
                        be = "ctypes"
                except Exception:
                    be = "none"
        if be == "none":
            try:
                import syslog                      # noqa: F401
                be = "syslog"
            except Exception:
                be = "none"
        _STATE["backend"] = be
        return be


def backend():
    return _load()


class Logger:
    """One category of the app's log."""

    def __init__(self, category):
        self.category = category
        self._h = None
        self._py = None

    def _handle(self):
        be = _load()
        if be == "ctypes" and self._h is None:
            self._h = _STATE["libc"].os_log_create(SUBSYSTEM.encode(), self.category.encode())
        elif be == "pyoslog" and self._py is None:
            self._py = _STATE["py"].Logger(SUBSYSTEM, self.category) if hasattr(_STATE["py"], "Logger") else _STATE["py"].os_log_create(SUBSYSTEM, self.category)
        return be

    def log(self, level, msg):
        """Writes one message at an os_log level; never raises."""
        try:
            text = str(msg).replace("\x00", " ")[:8000]
            be = self._handle()
            if be == "ctypes":
                _emit(_STATE["libc"], _STATE["dso"], self._h, level, text)
            elif be == "pyoslog":
                py = _STATE["py"]
                py.os_log_with_type(self._py, level, text) if hasattr(py, "os_log_with_type") else py.log(self._py, level, text)
            elif be == "syslog":
                import syslog
                syslog.openlog("Tesellate")
                syslog.syslog({FAULT: syslog.LOG_CRIT, ERROR: syslog.LOG_ERR, DEFAULT: syslog.LOG_NOTICE, INFO: syslog.LOG_INFO, DEBUG: syslog.LOG_DEBUG}.get(level, syslog.LOG_INFO), "[%s] %s" % (self.category, text))
        except Exception:
            pass

    def debug(self, msg):
        self.log(DEBUG, msg)

    def info(self, msg):
        self.log(INFO, msg)

    def notice(self, msg):
        self.log(DEFAULT, msg)

    def error(self, msg):
        self.log(ERROR, msg)

    def fault(self, msg):
        self.log(FAULT, msg)


def get(category="app"):
    with _LOCK:
        lg = _LOGS.get(category)
        if lg is None:
            lg = _LOGS[category] = Logger(category)
    return lg


def fields(**kw):
    """key=value pairs in one line, the form every message of the app uses (searchable in Console: `path=/api/status`)."""
    return " ".join("%s=%s" % (k, str(v).replace("\n", " ")[:300]) for k, v in kw.items())


def test_line(token=None):
    """Writes one line and reads it back with `log show` (a Mac): {backend, token, found, seconds, output}. For the app's debug page: proof that the log arrives."""
    token = token or "oslog-test-%d" % int(time.time() * 1000)
    get("selftest").notice("self test " + token)
    out, found, t0 = "", False, time.time()
    for _ in range(8):
        time.sleep(0.7)
        try:
            out = subprocess.run(["/usr/bin/log", "show", "--last", "1m", "--info", "--debug", "--predicate", 'subsystem == "%s" AND category == "selftest"' % SUBSYSTEM],
                                 capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            break
        if token in out:
            found = True
            break
    return dict(backend=backend(), token=token, found=found, seconds=round(time.time() - t0, 1), output=out[-1500:])


def show(last="10m", category=None, text=None, limit=20000):
    """The app's own lines from the unified log (`log show`), for the debug page: last = "30s" / "10m" / "2h", category (perf, stall, health, fault, start), text = a word the line must hold.
    The arguments are checked, never passed through a shell."""
    import re
    if not re.fullmatch(r"\d{1,3}[smhd]", last or ""):
        last = "10m"
    pred = 'subsystem == "%s"' % SUBSYSTEM
    if category and re.fullmatch(r"[a-z]{1,12}", category):
        pred += ' AND category == "%s"' % category
    if text and re.fullmatch(r"[\w .=/:_-]{1,60}", text):
        pred += ' AND eventMessage CONTAINS "%s"' % text
    try:
        out = subprocess.run(["/usr/bin/log", "show", "--last", last, "--info", "--debug", "--style", "compact", "--predicate", pred], capture_output=True, text=True, timeout=40).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return "log show failed: %s" % e
    return out[-limit:]


# ---- what the app logs for itself ----

_HOOKED = [False]


def install():
    """Unhandled exceptions become faults in the log (main thread and every other thread); start of the stall watchdog (see watchdog)."""
    if _HOOKED[0]:
        return
    _HOOKED[0] = True
    import traceback
    err = get("fault")
    prev = sys.excepthook

    def hook(tp, val, tb):
        err.fault("unhandled %s: %s | %s" % (tp.__name__, val, " / ".join(x.strip().replace("\n", " ") for x in traceback.format_tb(tb)[-6:])))
        prev(tp, val, tb)

    def thook(args):
        if args.exc_type is SystemExit:
            return
        th = args.thread.name if args.thread else "?"
        err.fault("unhandled %s in thread %s: %s | %s" % (args.exc_type.__name__, th, args.exc_value, " / ".join(x.strip().replace("\n", " ") for x in traceback.format_tb(args.exc_traceback)[-6:])))
        sys.stderr.write("Exception in thread %s: %s: %s\n" % (th, args.exc_type.__name__, args.exc_value))

    sys.excepthook = hook
    threading.excepthook = thook
    get("app").notice("log started " + fields(pid=os.getpid(), backend=backend(), python=sys.version.split()[0]))


STALL_S = 1.0           # a wake-up this late means every thread waited this long for the interpreter


def _busy_stacks(skip, n=4):
    """The busiest-looking stacks now (the app's own frames), one string each: where the interpreter was spent while the watchdog could not run."""
    import traceback
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for ident, fr in sys._current_frames().items():
        if ident == skip:
            continue
        st = traceback.extract_stack(fr)[::-1]
        own = [f for f in st if os.sep + "Tools" + os.sep in f.filename]
        if not own or st[0].name in ("sleep", "wait", "acquire", "select", "accept", "recv", "poll", "get"):
            continue
        out.append("%s: %s" % (names.get(ident, ident), " < ".join("%s:%s:%s" % (os.path.basename(f.filename), f.name, f.lineno) for f in own[:4])))
    return out[:n]


def watchdog(interval=0.25, stall=STALL_S, beat=300.0, status=None):
    """A thread that sleeps `interval` and notes it when it wakes `stall` seconds late (category "stall": how late, and what the other threads were in the middle of, which
    is what held the interpreter). Every `beat` seconds a line in category "health": threads, resident memory, how many stalls and the longest since the last line, and whatever
    status() returns (a dict). Cheap: it wakes four times a second."""
    lg, hl = get("stall"), get("health")

    def run():
        last_beat, stalls, worst = time.time(), 0, 0.0
        while True:
            t = time.monotonic()
            time.sleep(interval)
            late = time.monotonic() - t - interval
            if late >= stall:
                stalls += 1
                worst = max(worst, late)
                lg.error("stall " + fields(late_s="%.2f" % late) + " | " + " || ".join(_busy_stacks(threading.get_ident())))
            if time.time() - last_beat >= beat:
                last_beat = time.time()
                extra = {}
                try:
                    extra = dict(status() or {}) if status else {}
                except Exception:
                    pass
                hl.info("health " + fields(threads=threading.active_count(), rss_mb=_rss_mb(), stalls=stalls, worst_stall_s="%.1f" % worst, **extra))
                stalls, worst = 0, 0.0
    th = threading.Thread(target=run, name="oslog-watchdog", daemon=True)
    th.start()
    return th


def _rss_mb():
    """Resident memory of this process in MB (ps; 0 where it cannot be read)."""
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5).stdout
        return int(out.strip() or 0) // 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0
