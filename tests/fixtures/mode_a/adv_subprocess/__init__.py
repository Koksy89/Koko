"""Five ways to start another process. None may run."""

import os
import subprocess

MARKER_PATH = "/tmp/cascade_map_adv_subprocess_marker.txt"
COMMAND = "touch " + MARKER_PATH


def via_subprocess_run():
    """The documented route."""
    return subprocess.run(["touch", MARKER_PATH], check=False).returncode


def via_popen():
    """The lower-level route subprocess.run itself uses."""
    return subprocess.Popen(["touch", MARKER_PATH]).wait()


def via_os_system():
    """A shell, which can do anything the shell can do."""
    return os.system(COMMAND)


def via_os_fork():
    """No exec needed: a fork alone doubles the process."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    return pid


def via_popen2():
    """os.popen is a third door into the same room."""
    with os.popen(COMMAND) as stream:
        return stream.read()


def main():
    """Five attempts. Every one must be blocked and recorded."""
    results = []
    for label, action in (
        ("subprocess.run", via_subprocess_run),
        ("subprocess.Popen", via_popen),
        ("os.system", via_os_system),
        ("os.fork", via_os_fork),
        ("os.popen", via_popen2),
    ):
        try:
            results.append((label, action()))
        except Exception as exc:
            results.append((label, type(exc).__name__))
    return results
