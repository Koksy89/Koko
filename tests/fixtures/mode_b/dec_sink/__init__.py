"""A cascade ending in a declared decision sink, plus one element off the path."""


def load():
    """Ingestion."""
    return [1, 2, 3]


def score(rows):
    """Feature engineering."""
    return sum(rows)


def final_decision(rows):
    """THE SINK: the value this returns is the engine's answer."""
    if score(rows) > 5:
        return "enter"
    return "hold"


def log_metrics(rows):
    """Reaches no sink: nothing downstream of it feeds final_decision."""
    return {"count": len(rows)}


def main():
    """Entry point."""
    rows = load()
    log_metrics(rows)
    return final_decision(rows)
