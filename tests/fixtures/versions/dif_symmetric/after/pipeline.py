"""Before."""


def shared(value):
    """In both versions, unchanged."""
    return value


def only_after(value):
    """Present only here."""
    return value + 1


def tweaked(value):
    """Body differs between versions."""
    return value * 5
