"""Sentinel: writes a marker file the instant it is imported or executed.

This module is the empirical proof of constraint 1. Every static card asserts
the marker is absent after a full run; if analysis ever imports, execs, evals
or unpickles a target file, this module runs and the marker appears.

The marker path is fixed and absolute so a test can assert on it in one line,
and the write happens at module top level -- before any `if`, any `__main__`
guard, any function call -- so there is no way to import this module "a little
bit" without tripping it.
"""

import os

MARKER_PATH = "/tmp/cascade_map_sentinel_marker.txt"

with open(MARKER_PATH, "a", encoding="utf-8") as _handle:
    _handle.write("SENTINEL TRIPPED: imported\n")


def tripped() -> bool:
    """True when the marker exists. The one-line assertion for every card."""
    return os.path.exists(MARKER_PATH)


if __name__ == "__main__":
    with open(MARKER_PATH, "a", encoding="utf-8") as _handle:
        _handle.write("SENTINEL TRIPPED: executed as __main__\n")
