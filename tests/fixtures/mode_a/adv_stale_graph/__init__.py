"""A Mode B graph that does not match current target hashes is a refusal.

Same shape as adv_refuse_start: the program is ordinary and must not run. The
scenario for this case is set up by changing a target file after the Mode B
graph was built, so the graph hash no longer matches.
"""

MARKER_PATH = "/tmp/cascade_map_adv_stale_graph_marker.txt"

VERSION = 1


def main():
    """If this executes, a run overlaid a graph that no longer describes it."""
    with open(MARKER_PATH, "a", encoding="utf-8") as handle:
        handle.write("SCENARIO STARTED\n")
    return VERSION
