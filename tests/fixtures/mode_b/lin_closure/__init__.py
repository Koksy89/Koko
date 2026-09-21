"""A closure capturing a mutable cell, plus mutation through an alias."""


def make_counter(start):
    """`state` is captured by `bump` and also mutated through `alias`."""
    state = {"count": start}

    def bump(step):
        state["count"] = state["count"] + step
        return state["count"]

    alias = state
    alias["count"] = 0
    return bump
