"""Plain assignment, augmented assignment, unpacking and the walrus."""


def chain(seed):
    """Every binding form that moves a value from one name to another."""
    x = seed
    y = x + 1
    y += 10
    a, b = y, x
    if (total := a + b) > 0:
        return total
    return 0
