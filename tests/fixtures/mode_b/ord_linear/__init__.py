"""A chain whose order control flow fixes completely."""


def ingest():
    """Step 1."""
    return [1, 2, 3]


def clean(rows):
    """Step 2: cannot run before ingest, because it consumes its result."""
    return [row for row in rows if row]


def featurize(rows):
    """Step 3."""
    return sum(rows)


def decide(score):
    """Step 4: the final decision."""
    return "enter" if score > 3 else "hold"


def main():
    """The cascade, in one fixed order."""
    rows = ingest()
    cleaned = clean(rows)
    score = featurize(cleaned)
    return decide(score)
