"""Decorator-based registration into a module-level registry."""

_REGISTRY = {}


def register(name):
    """Return a decorator that files `func` under `name`."""

    def decorate(func):
        _REGISTRY[name] = func
        return func

    return decorate


@register("scale")
def scale_operation(value):
    """Registered under "scale" by the decorator."""
    return value * 2


@register("shift")
def shift_operation(value):
    """Registered under "shift" by the decorator."""
    return value + 1


def unregistered_operation(value):
    """Carries no decorator, so it is in no registry."""
    return value


def run(name, value):
    """Dispatch through the registry the decorators built."""
    return _REGISTRY[name](value)
