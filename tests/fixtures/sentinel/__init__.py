"""
Sentinel module: writes a marker file if imported or executed.

Static analysis must never import or execute this module. If it does,
this code runs and writes /tmp/cascade_map_sentinel_marker.txt.

The test suite asserts that this marker file is absent after every
static card completes.
"""

import os

_MARKER_FILE = "/tmp/cascade_map_sentinel_marker.txt"

# Write marker immediately upon import
with open(_MARKER_FILE, "w") as f:
    f.write("SENTINEL TRIGGERED\n")

# Also write if executed as main
if __name__ == "__main__":
    with open(_MARKER_FILE, "w") as f:
        f.write("SENTINEL TRIGGERED (main)\n")
