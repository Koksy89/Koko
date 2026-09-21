"""Before."""


def shared(value):
    """In both versions, unchanged."""
    return value


def only_before(value):
    """Present only here."""
    return value - 1


def tweaked(value):
    """Body differs between versions."""
    return value * 2
