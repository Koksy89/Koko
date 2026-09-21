"""Before: keep_me and drop_me."""


def keep_me(value):
    """Unchanged across versions."""
    return value + 1


def drop_me(value):
    """Removed in the after version."""
    return value - 1
