"""After: normalise lives here."""


def beta_only(rows):
    """Stays in beta."""
    return rows[0]


def normalise(rows):
    """Scale every row into 0..1."""
    span = max(rows) - min(rows)
    return [(row - min(rows)) / span for row in rows]
