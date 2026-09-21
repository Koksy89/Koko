"""The other half: b imports a."""

from . import a


def from_b():
    """Reach back into a."""
    return a.__name__
