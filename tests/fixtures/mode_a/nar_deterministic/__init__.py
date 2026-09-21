"""Nothing here varies between runs, so the narrative text must not either."""


def step_a(value):
    """First."""
    return value + 1


def step_b(value):
    """Second."""
    return value * 2


def main():
    """(1 + 1) * 2 = 4, every time."""
    return step_b(step_a(1))
