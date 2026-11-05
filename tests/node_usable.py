"""Whether `node` can run here: installed AND starting (a Homebrew node whose library was upgraded away fails to start, and its tests then skip instead of failing)."""
import shutil
import subprocess


def usable():
    if not shutil.which("node"):
        return False
    try:
        return subprocess.run(["node", "-e", "1"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
