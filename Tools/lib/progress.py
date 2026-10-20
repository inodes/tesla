"""progress.py - the one progress bar every long command uses.

    bar = Bar(total, "Reading")          # total may be 0/None when it is not known: then a count and speed only
    bar.update(n, "2026-10-12 11-29-55") # n done so far, and what is being done now
    bar.note("12: could not fetch")      # a line that must stay on screen (an error): printed above the bar
    bar.done("Read 8,449 clips.")        # finishes the line

On a terminal it is one line that updates in place:  Reading [#########-----------]  45%  3,800 of 8,449  1.8/s  41m left  <what>
Redirected to a file or a pipe (cron, the app) it prints a plain line about every 5 % or 30 s instead, so logs stay short.
"""
import sys
import time


def _n(x):
    return "{:,}".format(int(x))


def _dur(s):
    s = int(max(s, 0))
    return "%dh%02dm" % (s // 3600, s % 3600 // 60) if s >= 3600 else "%dm%02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


class Bar:
    def __init__(self, total=None, label="", out=None, width=24):
        self.total = int(total or 0)
        self.label = label
        self.out = out or sys.stdout
        self.width = width
        self.t0 = time.time()
        self.tty = bool(getattr(self.out, "isatty", lambda: False)())
        self._last_print = 0.0
        self._last_pct = -1
        self._shown = False

    def _line(self, n, tail):
        el = max(time.time() - self.t0, 1e-6)
        rate = n / el
        head = (self.label + " ") if self.label else ""
        if self.total:
            fill = min(self.width, int(self.width * n / self.total))
            left = (self.total - n) / rate if rate else 0
            s = "%s[%s%s] %3d%%  %s of %s  %.1f/s  %s left" % (head, "#" * fill, "-" * (self.width - fill), min(100, 100 * n // self.total), _n(n), _n(self.total), rate, _dur(left))
        else:
            s = "%s%s done  %.1f/s  %s" % (head, _n(n), rate, _dur(el))
        return s + ("  " + tail if tail else "")

    def update(self, n, tail=""):
        now = time.time()
        if self.tty:
            if now - self._last_print < 0.1 and n != self.total:
                return
            self._last_print = now
            self.out.write("\r" + self._line(n, tail) + "\033[K")
            self.out.flush()
            self._shown = True
        else:
            pct = 100 * n // self.total if self.total else -1
            if n == self.total or (self.total and pct >= self._last_pct + 5) or now - self._last_print >= 30 or not self._last_print:
                self._last_print, self._last_pct = now, pct
                self.out.write(self._line(n, tail) + "\n")
                self.out.flush()

    def note(self, text):
        """A line to keep (an error, a warning), printed above the bar."""
        if self.tty and self._shown:
            self.out.write("\r\033[K")
        self._shown = False
        self.out.write(text + "\n")
        self.out.flush()

    def done(self, text=""):
        if self.tty and self._shown:
            self.out.write("\n")
        self._shown = False
        if text:
            self.out.write(text + "\n")
        self.out.flush()
