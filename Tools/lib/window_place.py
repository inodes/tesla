"""window_place.py - the app's window comes back where it was, and never where it cannot be reached.

pywebview's own way of remembering a place does not survive two screens: the place it reports when the window moves is measured from one corner (the Mac's own, across all
screens) and the place it moves a window to is measured from another (the screen that has the keyboard when the app starts). A window remembered on the second screen and
started with that screen in front was put a whole screen further out: off every screen, at every start.

Here the window's frame is kept as the Mac itself has it (x, y, width, height in points, y upwards from the main screen's bottom-left corner, across all screens) and put
back with the Mac's own call, after a check against the screens there are now:

    fit(frame, screens) -> the frame to use: the same while its title bar can be reached (a strip of it lies on a screen, below the menu bar), else moved onto the screen
                           it overlaps most (shrunk to fit if it is bigger), or centred on the main screen when it overlaps none
    reachable(frame, screens) -> True when the title bar can be reached
    screens_now() -> [(x, y, w, h)] the usable area of every screen (without the menu bar and the Dock), the main screen first
    frame_of(win) -> the window's frame now, or None
    restore(win, saved) -> puts the window at `saved` (or leaves it) after fitting it to the screens; returns the frame it has
    watch(win) -> the same check whenever the screens change (one unplugged, the arrangement changed)
    to_main(win) -> the window in the middle of the main screen (Window > Bring to Main Screen in the menu bar)

The pure part (fit, reachable) has no Mac in it and is what the tests cover; on another system screens_now() is empty and nothing is moved.
"""
BAR = 28                # points: the height of a title bar
GRAB = 160              # points of title bar that must be on a screen for the window to count as reachable


def _overlap(a, b):
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


def reachable(frame, screens):
    x, y, w, h = frame
    top = y + h
    for sx, sy, sw, sh in screens:
        across = min(x + w, sx + sw) - max(x, sx)
        if across >= min(GRAB, w) and sy + BAR <= top <= sy + sh + 1:
            return True
    return False


def fit(frame, screens):
    frame = tuple(float(v) for v in frame)
    if not screens or reachable(frame, screens):
        return frame
    best = max(screens, key=lambda s: _overlap(frame, s))
    on = _overlap(frame, best) > 0
    if not on:
        best = screens[0]
    sx, sy, sw, sh = best
    w, h = min(frame[2], sw), min(frame[3], sh)
    if on:                                                  # pushed back onto the screen it is partly on
        x = min(max(frame[0], sx), sx + sw - w)
        y = min(max(frame[1], sy), sy + sh - h)
    else:                                                   # on no screen at all: the middle of the main one
        x, y = sx + (sw - w) / 2.0, sy + (sh - h) / 2.0
    return (float(round(x)), float(round(y)), float(round(w)), float(round(h)))


def nswindow(win):
    """The Mac's own window object of a pywebview window (pywebview 6 gives it as win.native; older ones wrapped it)."""
    n = getattr(win, "native", None)
    if n is None:
        return None
    return n if hasattr(n, "setFrame_display_") else getattr(n, "window", None)


def screens_now():
    try:
        import AppKit
        out = []
        for s in AppKit.NSScreen.screens():                 # the first is the one with the menu bar, whose bottom-left corner is (0, 0)
            v = s.visibleFrame()
            out.append((v.origin.x, v.origin.y, v.size.width, v.size.height))
        return out
    except Exception:
        return []


def frame_of(win):
    try:
        f = nswindow(win).frame()
        return (f.origin.x, f.origin.y, f.size.width, f.size.height)
    except Exception:
        return None


def _on_main(fn):
    try:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(fn)
    except Exception:
        fn()


def restore(win, saved, after=None):
    """Puts the window where it was (`saved` = [x, y, w, h] or None for where it is now), fitted to the screens there are; after(frame) is told what it ended up as."""
    def go():
        try:
            w = nswindow(win)
            cur = frame_of(win)
            scr = screens_now()
            if w is None or cur is None or not scr:
                return
            want = tuple(saved) if saved and len(saved) == 4 else cur
            to = fit(want, scr)
            if tuple(round(v) for v in to) != tuple(round(v) for v in cur):
                import AppKit
                w.setFrame_display_(AppKit.NSMakeRect(*to), True)
            if after:
                after(to)
        except Exception:
            pass
    _on_main(go)


_WATCH = []


def watch(win, after=None):
    """When the screens change, a window left on none of them is brought back (the Mac usually does this itself; this is for when it does not)."""
    if _WATCH:
        return True                                         # once is enough, however often the window is shown
    try:
        import AppKit
        tok = AppKit.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            AppKit.NSApplicationDidChangeScreenParametersNotification, None, AppKit.NSOperationQueue.mainQueue(), lambda note: restore(win, None, after))
        _WATCH.append(tok)                                  # kept: the observer lives as long as the app
        return True
    except Exception:
        return False


def centred(size, screen):
    """A frame of this (width, height) in the middle of a screen's usable area, shrunk to it when bigger."""
    sx, sy, sw, sh = screen
    w, h = min(size[0], sw), min(size[1], sh)
    return (float(round(sx + (sw - w) / 2.0)), float(round(sy + (sh - h) / 2.0)), float(round(w)), float(round(h)))


def to_main(win, after=None):
    """The menu's way back: the window, at the size it has, in the middle of the main screen."""
    cur, scr = frame_of(win), screens_now()
    if cur and scr:
        restore(win, centred((cur[2], cur[3]), scr[0]), after)
