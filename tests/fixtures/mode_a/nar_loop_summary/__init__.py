"""A thousand identical iterations must be summarised, not transcribed."""

ITERATIONS = 1000


def accumulate(total, value):
    """Called once per iteration."""
    return total + value


def main():
    """1000 iterations; the total is 0+1+...+999 = 499500."""
    total = 0
    for value in range(ITERATIONS):
        total = accumulate(total, value)
    return total
