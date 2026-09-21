"""The same name defined twice at module level: the first can never run."""


def compute(value):
    """First definition. Shadowed by the one below; no call can ever reach it."""
    return value * 2


def compute(value):
    """Second definition. This is what the name is bound to."""
    return value * 3


def main(value):
    """Calls the surviving definition."""
    return compute(value)
