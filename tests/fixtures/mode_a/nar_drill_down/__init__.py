"""Three levels of call depth: the narrative nests, it does not flatten."""


def inner(value):
    """Depth 3."""
    return value + 1


def middle(value):
    """Depth 2."""
    return inner(value) * 2


def outer(value):
    """Depth 1."""
    return middle(value) + 10


def main():
    """Depth 0. (1 + 1) * 2 + 10 = 14."""
    return outer(1)
