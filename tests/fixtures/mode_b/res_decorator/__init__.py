"""A decorator: calls must reach both the wrapper and the wrapped function."""


def trace(func):
    """Wrap `func`."""

    def wrapper(value):
        """What the caller actually invokes."""
        return func(value)

    return wrapper


@trace
def compute(value):
    """What the caller thinks it is invoking."""
    return value * 2


def caller():
    """Call the decorated name."""
    return compute(21)
