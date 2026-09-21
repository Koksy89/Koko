"""Three ways out of a sandbox directory. All must land inside it or be blocked.

Absolute path, parent traversal, and a symlink pointing out. Each writes a
distinct marker so a failure names which route got through.
"""

import os

ABSOLUTE_MARKER = "/tmp/cascade_map_adv_absolute_marker.txt"
TRAVERSAL_MARKER = "../../cascade_map_adv_traversal_marker.txt"
SYMLINK_NAME = "escape_link"
SYMLINK_TARGET = "/tmp"


def write_absolute():
    """Straight at an absolute path outside the sandbox."""
    with open(ABSOLUTE_MARKER, "w", encoding="utf-8") as handle:
        handle.write("absolute write escaped\n")
    return ABSOLUTE_MARKER


def write_traversal():
    """Climb out with `..`."""
    with open(TRAVERSAL_MARKER, "w", encoding="utf-8") as handle:
        handle.write("traversal write escaped\n")
    return TRAVERSAL_MARKER


def write_through_symlink():
    """Create a symlink out of the sandbox, then write through it."""
    if not os.path.islink(SYMLINK_NAME):
        os.symlink(SYMLINK_TARGET, SYMLINK_NAME)
    path = os.path.join(SYMLINK_NAME, "cascade_map_adv_symlink_marker.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("symlink write escaped\n")
    return path


def main():
    """Three escape routes. Each is blocked, or redirected into the sandbox."""
    results = []
    for label, action in (
        ("absolute", write_absolute),
        ("traversal", write_traversal),
        ("symlink", write_through_symlink),
    ):
        try:
            results.append((label, action()))
        except Exception as exc:
            results.append((label, type(exc).__name__))
    return results
