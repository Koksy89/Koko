"""After: keep_me and add_me."""


def keep_me(value):
    """Unchanged across versions."""
    return value + 1


def add_me(value):
    """Added in the after version."""
    return value * 3
