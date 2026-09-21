"""Exports `alpha` and `beta`; `delta` is deliberately left out of __all__."""

__all__ = ["alpha", "beta"]


def alpha():
    """Exported."""
    return "alpha"


def beta():
    """Exported."""
    return "beta"


def delta():
    """Defined but NOT exported: `import *` does not bind this name."""
    return "delta"
