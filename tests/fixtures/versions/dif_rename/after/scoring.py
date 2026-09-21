"""After: the same function is called calculate_score."""


def calculate_score(rows, weight):
    """Weighted total."""
    total = 0
    for row in rows:
        total = total + row * weight
    return total


def report(rows):
    """Caller, updated in the after version."""
    return {"score": calculate_score(rows, 2)}
