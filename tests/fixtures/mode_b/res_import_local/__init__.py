"""An import that lives inside a function body, not at module level."""


def encode(payload):
    """Import at call time, not at module import time."""
    from .codec import to_text

    return to_text(payload)
