"""`and`/`or` are branches: each operand may never be evaluated."""


def check(a, b, c):
    """`b` is evaluated only if `a` is truthy; `c` only if `a and b` is falsy."""
    return a and b or c


def gated(flag, value):
    """`expensive` is called only when flag is truthy: a real branch, not a call."""
    return flag and expensive(value)


def expensive(value):
    """Reached only through the right-hand side of the `and`."""
    return value * 1000
