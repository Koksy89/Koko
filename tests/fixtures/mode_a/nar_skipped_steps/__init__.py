"""Three guards; the scenario trips the first, so two stages never run."""


def validate(payload):
    """Returns None for an empty payload, which short-circuits the pipeline."""
    if not payload:
        return None
    return payload


def enrich(payload):
    """Skipped in this scenario."""
    return payload + [0]


def score(payload):
    """Skipped in this scenario."""
    return sum(payload)


def main():
    """An empty payload stops the pipeline at the first stage."""
    payload = validate([])
    if payload is None:
        return "rejected"
    return score(enrich(payload))
