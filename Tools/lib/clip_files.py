"""clip_files.py - the footage the player is showing, as files: show them in Finder, or copy a range of them (every camera) to a folder the person picks.

The page names clips by the address it plays them from (/footage/<drive>/<path>); `resolve` turns those into paths on the connected drives and nothing else
(the same rule as playing them: a connected drive, a video file, inside that drive). Copying runs in the background and is asked after by job id.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

MAX_REVEAL = 60
COPY_EXT = (".mp4",)


def resolve(urls, footage_root, safe_join):
    """[/footage/<role>/<rel>, ...] -> [path] of the files that exist (in order, no repeats); anything else is left out."""
    out, seen = [], set()
    for u in urls or []:
        u = urllib.parse.unquote(str(u).split("?")[0])
        if not u.startswith("/footage/"):
            continue
        role, _, rel = u[9:].partition("/")
        root = footage_root(role)
        if not root or not rel.lower().endswith(COPY_EXT):
            continue
        p = safe_join(root, rel)
        if p and os.path.isfile(p) and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def reveal(paths):
    """Show these files selected in Finder."""
    if sys.platform != "darwin":
        return dict(ok=False, error="Showing a file in Finder works on a Mac only.")
    if not paths:
        return dict(ok=False, error="That video is not on a connected drive.")
    subprocess.run(["open", "-R"] + list(paths[:MAX_REVEAL]), timeout=30, check=False)
    return dict(ok=True, shown=min(len(paths), MAX_REVEAL))


def open_folder(path):
    if sys.platform != "darwin" or not os.path.isdir(path):
        return dict(ok=False, error="That folder is not there.")
    subprocess.run(["open", path], timeout=30, check=False)
    return dict(ok=True)


def choose_folder(prompt="Copy the videos to which folder?"):
    """A folder picked in the macOS dialog ('' when it is cancelled or there is no dialog)."""
    if sys.platform != "darwin":
        return ""
    esc = prompt.replace("\\", "\\\\").replace('"', '\\"')
    try:
        r = subprocess.run(["osascript", "-e", 'tell application "System Events"', "-e", "activate", "-e", 'set f to choose folder with prompt "%s"' % esc,
                            "-e", "return POSIX path of f", "-e", "end tell"], capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


class Copier:
    """Copy jobs: {job: {state: running|done|error, n, done, bytes, bytes_done, dest, skipped}}. One folder per copy, named by the caller, never an existing one."""

    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, paths, dest_parent, name):
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return dict(ok=False, error="None of those videos is on a connected drive.")
        if not os.path.isdir(dest_parent):
            return dict(ok=False, error="That folder is not there.")
        safe = "".join(c if c not in '/\\:*?"<>|' else "-" for c in (name or "TeslaCam clips")).strip() or "TeslaCam clips"
        dest, k = os.path.join(dest_parent, safe), 1
        while os.path.exists(dest):
            k += 1
            dest = os.path.join(dest_parent, "%s (%d)" % (safe, k))
        os.makedirs(dest)
        sizes = [os.path.getsize(p) for p in paths]
        job = uuid.uuid4().hex[:10]
        st = dict(state="running", n=len(paths), done=0, bytes=sum(sizes), bytes_done=0, dest=dest, failed=[])
        with self.lock:
            self.jobs[job] = st
        threading.Thread(target=self._run, args=(st, paths, dest), daemon=True).start()
        return dict(ok=True, job=job, **self.view(st))

    def _run(self, st, paths, dest):
        for p in paths:
            try:
                shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
                st["bytes_done"] += os.path.getsize(p)
            except OSError as e:
                st["failed"].append("%s: %s" % (os.path.basename(p), e))
            st["done"] += 1
        st["state"] = "error" if st["failed"] and len(st["failed"]) == st["n"] else "done"
        st["finished"] = time.time()

    @staticmethod
    def view(st):
        return {k: v for k, v in st.items() if k != "finished"}

    def status(self, job):
        with self.lock:
            st = self.jobs.get(job)
        return dict(ok=True, **self.view(st)) if st else dict(ok=False, error="Unknown copy.")

    def dest_of(self, job):
        with self.lock:
            st = self.jobs.get(job)
        return st["dest"] if st else ""
