"""A whole small cascade, for the one-paragraph summary at the top."""


def ingest():
    """Stage 1."""
    return [2, 4, 6]


def clean(rows):
    """Stage 2."""
    return [row for row in rows if row > 2]


def featurize(rows):
    """Stage 3."""
    return {"total": sum(rows), "count": len(rows)}


def decide(features):
    """Stage 4: the decision."""
    return "enter" if features["total"] > 8 else "hold"


def main():
    """clean drops the 2; total is 10; 10 > 8, so the answer is 'enter'."""
    return decide(featurize(clean(ingest())))
