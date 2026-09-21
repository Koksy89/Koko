"""Every narrative step must name the element and the events behind it."""


def load():
    """Ingestion phase."""
    return [1, 2, 3]


def featurize(rows):
    """Feature phase."""
    return sum(rows)


def decide(score):
    """Decision phase."""
    return "enter" if score > 5 else "hold"


def main():
    """Three phases, in order."""
    return decide(featurize(load()))
