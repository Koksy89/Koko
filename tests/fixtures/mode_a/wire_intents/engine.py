"""Six elements, five verdicts, one owner-confirmed intents file.

This case exists to prove the WIRING: that `analyze --intents` and `trace
--intents` reach card 13 at all, and that every verdict survives the journey
from the owner's YAML to `intents.jsonl`, `verdicts.jsonl` and the viewer.

The owner's question this answers is the one the whole registry is for:
"which of the things I built are not plugged in?" `declared_live_strategy`
below is exactly that shape -- a complete, working, importable function that
nothing in the cascade calls. Dead-code detection guesses it from structure.
The intents file states it as the owner's own declaration, so the verdict is
a fact about what they said, not an inference from a name.

`decide` is the decision sink.
"""


def helper_on_path(value):
    """On the path to the decision, and the intent says only that."""
    return value + 0


def score(value):
    """Doubles its argument. The intent says so and the run agrees."""
    return value * 2


def gate(value):
    """The intent says this never returns a negative number. It does."""
    return value - 100


def declared_live_strategy(value):
    """The owner declares this live. Nothing in this module calls it.

    Not dead by inspection -- it is complete, correct and importable. It is
    unplugged, and only the owner's declaration that it should be plugged in
    makes that a finding rather than an opinion.
    """
    return value * 7


def never_run(value):
    """Called by nothing in this scenario. Unverified is not correct."""
    return value + 1


def undeclared_helper(value):
    """No intent names this. NO_INTENT is a state, not a failure."""
    return value


def decide():
    """The decision sink. Runs helper_on_path, then score, then gate."""
    return gate(score(helper_on_path(undeclared_helper(3))))


def main():
    """The scenario entry point."""
    return decide()
