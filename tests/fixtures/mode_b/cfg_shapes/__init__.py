"""Module demonstrating various CFG shapes."""


def branching_code(x):
    """If/else branch."""
    if x > 0:
        return "positive"
    else:
        return "negative"


def loop_code(items):
    """For loop."""
    total = 0
    for item in items:
        total += item
    return total


def exception_handling(data):
    """Try/except block."""
    try:
        result = int(data)
        return result
    except ValueError:
        return 0


def with_statement(filename):
    """With statement (context manager)."""
    with open(filename, 'r') as f:
        return f.read()
