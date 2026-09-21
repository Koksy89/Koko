"""Exceptions are recorded whether they escape or are swallowed."""


def risky(value):
    """Raises for negative input."""
    if value < 0:
        raise ValueError("negative input")
    return value


def guarded(value):
    """Swallows the ValueError and returns a default."""
    try:
        return risky(value)
    except ValueError:
        return 0


def main():
    """One swallowed exception, then one clean call."""
    return guarded(-1), guarded(7)
