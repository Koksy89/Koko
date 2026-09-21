"""Two functions with structurally identical bodies, and one that differs."""


def score_alpha(rows):
    """Weighted sum."""
    total = 0
    for row in rows:
        total = total + row * 2
    return total


def score_beta(rows):
    """Weighted sum, copy-pasted."""
    total = 0
    for row in rows:
        total = total + row * 2
    return total


def score_gamma(rows):
    """Similar shape, different arithmetic: not a duplicate."""
    total = 0
    for row in rows:
        total = total + row * 3
    return total


def run(rows):
    """All three are called, so none of them is unreachable."""
    return score_alpha(rows), score_beta(rows), score_gamma(rows)
