"""A branch whose condition can never be true.

DEBUG is bound once, at module level, to a literal False, and nothing in the
target rebinds it. The body of `if DEBUG:` is therefore unreachable.
"""

DEBUG = False


def route(value):
    """One dead arm, one live branch."""
    if DEBUG:
        return "debug"
    if value > 0:
        return "positive"
    return "non_positive"
