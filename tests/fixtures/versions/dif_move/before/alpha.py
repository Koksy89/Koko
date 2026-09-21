"""Before: normalise lives here."""


def normalise(rows):
    """Scale every row into 0..1."""
    span = max(rows) - min(rows)
    return [(row - min(rows)) / span for row in rows]


def alpha_only(rows):
    """Stays in alpha."""
    return len(rows)
