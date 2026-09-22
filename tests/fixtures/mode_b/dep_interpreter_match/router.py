"""A match statement in a project that promises Python 3.8."""


def route(signal):
    match signal:
        case {"kind": "buy"}:
            return "BUY"
        case _:
            return "HOLD"


def older(a, b, /):
    return a + b
