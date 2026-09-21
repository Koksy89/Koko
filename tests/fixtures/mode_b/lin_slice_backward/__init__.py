"""A backward slice must be exact: it excludes what does not feed the root."""


def compute(a, b, noise):
    """`noise` never reaches `z`."""
    x = a + 1
    y = b * 2
    unused = noise * 99
    z = x + y
    return z


def decide(a, b, noise):
    """THE SINK."""
    return "enter" if compute(a, b, noise) > 5 else "hold"
