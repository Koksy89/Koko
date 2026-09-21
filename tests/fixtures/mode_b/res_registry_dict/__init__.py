"""A module-level dict of callables, dispatched by key."""


def handle_buy(order):
    """Registered under "buy"."""
    return order + 1


def handle_sell(order):
    """Registered under "sell"."""
    return order - 1


def handle_unregistered(order):
    """Defined but absent from HANDLERS."""
    return order


HANDLERS = {
    "buy": handle_buy,
    "sell": handle_sell,
}


def dispatch(action, order):
    """Look the handler up by key and call it."""
    return HANDLERS[action](order)
