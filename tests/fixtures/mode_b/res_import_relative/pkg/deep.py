"""Two levels up: `..` reaches the package root's sibling."""

from ..sibling import helper


def deep_call():
    """Use the twice-relative import."""
    return helper()
