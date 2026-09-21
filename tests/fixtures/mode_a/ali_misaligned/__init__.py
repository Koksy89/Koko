"""The code contradicts its confirmed intent, on one specific point."""


def apply_fee(amount):
    """Says it floors at zero. It does not: the floor is missing."""
    return amount - 2


def main():
    """The second call returns -1, which the intent forbids."""
    return apply_fee(10), apply_fee(1)
