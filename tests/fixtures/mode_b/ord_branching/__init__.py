"""Two mutually exclusive paths that rejoin."""


def aggressive(value):
    """Taken only when the flag is set."""
    return value * 2


def conservative(value):
    """Taken only when the flag is not set."""
    return value // 2


def report(value):
    """Runs on both paths: the merge point."""
    return {"value": value}


def main(flag, value):
    """Branch, then merge."""
    if flag:
        scored = aggressive(value)
    else:
        scored = conservative(value)
    return report(scored)
