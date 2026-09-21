"""Before."""


def price(value):
    """Signature changes in the after version; body does not."""
    return value * 100


def convert(value):
    """Body changes in the after version; signature does not."""
    return value / 2


def steady(value):
    """Neither changes."""
    return value
