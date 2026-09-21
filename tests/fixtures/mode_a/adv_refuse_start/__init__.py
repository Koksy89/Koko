"""A perfectly ordinary program. It must never get the chance to run.

This case is not about the program: it is about the harness refusing to start
when a control cannot be verified. `main` writes a marker as its very first
statement, so "the run refused" and "the run started and did nothing" are
distinguishable by one file check.
"""

MARKER_PATH = "/tmp/cascade_map_adv_refuse_start_marker.txt"


def main():
    """If this ever executes, the harness started when it should not have."""
    with open(MARKER_PATH, "a", encoding="utf-8") as handle:
        handle.write("SCENARIO STARTED\n")
    return "started"
