"""What did NOT happen is part of the story.

Two things are absent from this run: the `escalate` branch is never taken, and
the write to /tmp is blocked by the harness. Both belong in the narrative.
"""

BLOCKED_PATH = "/tmp/cascade_map_nar_absence_marker.txt"


def escalate(score):
    """The branch this scenario never takes."""
    return "escalate"


def settle(score):
    """The branch this scenario always takes."""
    return "settle"


def route(score):
    """score is 1 in this scenario, so the escalate arm never runs."""
    if score > 100:
        return escalate(score)
    return settle(score)


def persist(verdict):
    """Attempts a write outside the sandbox. It must be blocked."""
    with open(BLOCKED_PATH, "a", encoding="utf-8") as handle:
        handle.write(verdict + "\n")
    return BLOCKED_PATH


def main():
    """Route, then try to persist."""
    verdict = route(1)
    try:
        persist(verdict)
    except Exception as exc:
        return verdict, type(exc).__name__
    return verdict, "PERSISTED"
