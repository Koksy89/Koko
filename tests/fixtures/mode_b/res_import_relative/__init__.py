"""One level: `.` reaches the sibling module."""

from . import sibling


def local_call():
    """Use the once-relative import."""
    return sibling.helper()
