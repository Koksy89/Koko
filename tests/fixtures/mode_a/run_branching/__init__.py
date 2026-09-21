"""The branch actually taken is recorded at each decision point."""


def classify(value):
    """Two conditions; which arm runs is decided by the argument."""
    if value > 10:
        return "high"
    if value > 0:
        return "low"
    return "negative"


def main():
    """Three calls, exercising all three arms in a known order."""
    return [classify(50), classify(5), classify(-1)]
