"""Fully deterministic: the same inputs give the same trace, every time."""


def tally(rows):
    """Pure function of its argument."""
    total = 0
    for row in rows:
        total = total + row
    return total


def main():
    """No clock, no RNG, no I/O, no environment."""
    return tally([1, 2, 3, 4])
