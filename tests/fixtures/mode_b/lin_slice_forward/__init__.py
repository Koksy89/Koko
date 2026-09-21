"""A forward slice reaches the sink and stops there."""


def transform(raw):
    """Two hops from the input to the value the sink tests."""
    doubled = raw * 2
    shifted = doubled + 1
    return shifted


def decide(raw):
    """THE SINK. Nothing downstream of this is part of the forward slice."""
    return "enter" if transform(raw) > 5 else "hold"


def audit(raw):
    """Consumes the same input but feeds nothing the sink reads."""
    return {"seen": raw}


def after_the_sink(verdict):
    """Called by the caller of decide; not reachable from `raw`."""
    return verdict.upper()
