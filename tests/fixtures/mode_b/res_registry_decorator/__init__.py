"""Test decorator-based registration."""
_registry = {}

def register(name):
    def decorator(func):
        _registry[name] = func
        return func
    return decorator

@register("op")
def operation():
    return 42
