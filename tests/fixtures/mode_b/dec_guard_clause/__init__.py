"""Guard clauses: each early return is a decision with one exit outcome."""


def validate(payload, limit):
    """Three guards, then the happy path."""
    if payload is None:
        return "missing"
    if not payload:
        return "empty"
    if len(payload) > limit:
        return "too_large"
    return "ok"
