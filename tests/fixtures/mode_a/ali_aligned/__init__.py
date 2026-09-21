"""The code does what the confirmed intent says it does."""


def apply_fee(amount):
    """Subtract a flat fee of 2 and never return a negative amount."""
    return max(amount - 2, 0)


def main():
    """Two calls: one ordinary, one that exercises the floor."""
    return apply_fee(10), apply_fee(1)
