"""Half of an import cycle: a imports b."""

from . import b


def from_a():
    """Reach into b."""
    return b.from_b()
