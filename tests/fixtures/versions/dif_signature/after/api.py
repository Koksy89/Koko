"""Before."""


def price(value, currency="USD"):
    """Signature changes in the after version; body does not."""
    return value * 100


def convert(value):
    """Body changes in the after version; signature does not."""
    return value / 4


def steady(value):
    """Neither changes."""
    return value
