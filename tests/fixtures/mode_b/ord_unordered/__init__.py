"""Two independent side effects whose relative order nothing fixes."""


def audit(payload):
    """Independent of `notify`: neither reads the other's result."""
    return len(payload)


def notify(payload):
    """Independent of `audit`."""
    return payload


LISTENERS = {audit, notify}


def broadcast(payload):
    """Iterating a set: the order is not a program order the analysis can fix."""
    for listener in LISTENERS:
        listener(payload)
