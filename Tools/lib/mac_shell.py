"""mac_shell.py - the parts of the app that make it behave like a Mac app around its window (step 1 of docs/NATIVE_MAC_APP.md).

Everything that decides something is plain Python and is tested here (the menu, the Dock badge text, when a job's end is worth a notification); the few
calls that touch macOS (the menu bar through pywebview, the Dock tile through AppKit, a notification through osascript) are wrapped so that a Mac without
pyobjc or pywebview, or any other system, simply goes without them.

    PAGES                       the pages of the side bar: (title, route)
    menu_spec()                 [(menu title, [(item title, route)])] for the menu bar: Go, with every page
    badge_text(keeper, ckeep)   the text for the Dock icon while a job runs ("45%"), "" when nothing runs
    Watcher(notify).tick(label, running, now) -> None, or (title, text) when a job that ran long enough has just ended
    start(window, keeper_fn, ckeep_fn)  starts the thread that keeps the Dock badge and the notifications up to date
"""
import subprocess
import sys
import threading
import time

PAGES = [("Activity", "activity"), ("Drives", "drives"), ("Drives (new)", "driveslist"), ("Charges", "charges"), ("Places", "places"), ("Alerts", "alerts"),
         ("Software", "software"), ("Battery", "battery"), ("Analytics", "analytics"), ("TeslaCam", "teslacam"), ("Queue", "queue")]
NOTIFY_MIN_S = 60                       # a job shorter than this ends without a notification


def menu_spec():
    return [("Go", [(t, r) for t, r in PAGES])]


def _pct(done, total):
    return max(0, min(100, int(round(100.0 * (done or 0) / total)))) if total else None


def badge_text(keeper, ckeep):
    """What the Dock icon says: the share done of the background reading (Keeper), else of the delete / move / process job (ClipKeep); '' when neither runs; '…' when it runs without a total."""
    if keeper and keeper.get("working"):
        p = _pct(keeper.get("done"), keeper.get("total"))
        return "%d%%" % p if p is not None else "…"
    if ckeep and ckeep.get("running"):
        p = _pct(ckeep.get("step_done"), ckeep.get("step_total"))
        return "%d%%" % p if p is not None else "…"
    return ""


def label_of(keeper, ckeep):
    """A few words for what is running, for the notification when it ends."""
    if keeper and keeper.get("working"):
        return keeper.get("text") or "Reading the drives"
    if ckeep and ckeep.get("running"):
        return ckeep.get("step") or "Working on the clips"
    return ""


class Watcher:
    """Remembers when a job started; when it ends after at least NOTIFY_MIN_S, says what to tell the person."""
    def __init__(self, min_s=NOTIFY_MIN_S):
        self.min_s, self.since, self.label = min_s, None, ""

    def tick(self, label, running, now):
        if running:
            if self.since is None:
                self.since = now
            self.label = label or self.label
            return None
        since, lab = self.since, self.label
        self.since, self.label = None, ""
        if since is not None and now - since >= self.min_s:
            m = int((now - since) // 60)
            return ("Tesellate", "%s finished (%d min)." % (lab or "The job", max(1, m)))
        return None


def notify(title, text):
    """A notification in the Notification Centre, through osascript (no extra package); nothing happens off a Mac."""
    if sys.platform != "darwin":
        return False
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s"' % (esc(text), esc(title))], timeout=10, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def set_badge(text):
    """The text on the Dock icon ('' clears it). Done on the main thread, as AppKit needs; False when AppKit is not there."""
    try:
        import AppKit
        from PyObjCTools import AppHelper
    except Exception:
        return False
    AppHelper.callAfter(lambda: AppKit.NSApplication.sharedApplication().dockTile().setBadgeLabel_(text or None))
    return True


def build_menu(window):
    """The menu bar for pywebview's start(menu=...): None when this pywebview has no menus. Choosing a page sets the route of the window."""
    try:
        from webview.menu import Menu, MenuAction
    except Exception:
        return None

    def go(route):
        return lambda: window.evaluate_js("location.hash=%r" % ("#/" + route))
    return [Menu(t, [MenuAction(it, go(r)) for it, r in items]) for t, items in menu_spec()]


def start(keeper_fn, ckeep_fn, interval=2.0, set_badge_fn=set_badge, notify_fn=notify, clock=time.time):
    """Keeps the Dock badge and the notifications up to date from the two job states (functions returning the dicts the app serves), in a thread of its own."""
    w = Watcher()

    def loop():
        last = None
        while True:
            try:
                k, c = keeper_fn(), ckeep_fn()
                t = badge_text(k, c)
                if t != last:
                    set_badge_fn(t)
                    last = t
                msg = w.tick(label_of(k, c), bool(t), clock())
                if msg:
                    notify_fn(*msg)
            except Exception:
                pass
            time.sleep(interval)
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return th
