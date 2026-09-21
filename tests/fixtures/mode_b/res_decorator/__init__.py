"""Test decorator resolution."""
def decorator(func):
    def wrapper():
        return func()
    return wrapper

@decorator
def decorated():
    return 42
