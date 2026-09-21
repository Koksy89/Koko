"""Every intermediate value here is fixed by the source."""


def add(a, b):
    """Sum two numbers."""
    return a + b


def scale(value, factor):
    """Multiply."""
    return value * factor


def main():
    """2 + 3 = 5; 5 * 4 = 20; 20 + 1 = 21."""
    total = add(2, 3)
    scaled = scale(total, 4)
    return add(scaled, 1)
