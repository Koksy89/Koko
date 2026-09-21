"""Before: the function is called compute_score."""


def compute_score(rows, weight):
    """Weighted total."""
    total = 0
    for row in rows:
        total = total + row * weight
    return total


def report(rows):
    """Caller, updated in the after version."""
    return {"score": compute_score(rows, 2)}
