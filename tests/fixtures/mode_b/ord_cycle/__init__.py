"""Direct recursion and mutual recursion: two cycles, with their members."""


def countdown(n):
    """Calls itself: a one-member cycle."""
    if n > 0:
        return countdown(n - 1)
    return 0


def ping(n):
    """Half of a two-member cycle."""
    if n <= 0:
        return "done"
    return pong(n - 1)


def pong(n):
    """The other half."""
    return ping(n - 1)
