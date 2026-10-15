"""app_sync.py - run sync_charger_all.sh from the Tesla app and show its output as it arrives (REQ-206).

The script is the repo's one command for the Chargers domain (Tesla Superchargers and Destination chargers, Chargefox, EVX, Evie,
bp pulse, PlugShare, AmpCharge, Exploren, Jolt, NRMA). It writes charging_stations.json and the other charger files in the data
folder (TESLA_DATA_DIR is set to the app's data folder). It needs the network, can take many minutes, and runs only when you press Run.
"""
import collections
import os
import signal
import subprocess
import sys
import threading
import time


class ChargerSync:
    def __init__(self, tools_dir, data_dir):
        self.tools, self.data = tools_dir, data_dir
        self.script = os.path.join(tools_dir, "sync_charger_all.sh")
        self.lines = collections.deque(maxlen=4000)
        self.n = 0                    # lines ever received (the poll cursor)
        self.proc = None
        self.started = self.ended = None
        self.rc = None
        self.lock = threading.Lock()

    def _pump(self, proc):
        for line in proc.stdout:
            with self.lock:
                self.lines.append(line.rstrip("\n"))
                self.n += 1
        proc.wait()
        with self.lock:
            self.rc, self.ended = proc.returncode, time.time()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        with self.lock:
            if self.running():
                return self.status_locked(0)
        if not os.path.isfile(self.script):
            return dict(ok=False, error="sync_charger_all.sh not found in " + self.tools)
        env = dict(os.environ, TESLA_DATA_DIR=self.data, PYTHONUNBUFFERED="1")
        env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")      # the repo venv first, as direnv would
        with self.lock:
            self.lines.clear()
            self.n, self.rc, self.ended, self.started = 0, None, None, time.time()
        self.proc = subprocess.Popen(["bash", self.script], cwd=self.tools, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        return self.status(0)

    def stop(self):
        if self.running():
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except OSError:
                pass
            with self.lock:
                self.lines.append("(stopped from the app)")
                self.n += 1
        return self.status(0)

    def status_locked(self, since):
        first = self.n - len(self.lines)
        lines = list(self.lines)[max(0, since - first):]
        return dict(ok=True, running=self.running(), started=self.started, ended=self.ended, rc=self.rc, n=self.n, lines=lines)

    def status(self, since=0):
        with self.lock:
            return self.status_locked(since)
