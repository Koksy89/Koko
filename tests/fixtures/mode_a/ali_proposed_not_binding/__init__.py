"""A PROPOSED intent cannot ground a verdict, even when the code contradicts it."""


def rebate(amount):
    """Docstring claims it halves the amount. It does not: it subtracts one."""
    return amount - 1


def main():
    """rebate(10) returns 9, not 5."""
    return rebate(10)
