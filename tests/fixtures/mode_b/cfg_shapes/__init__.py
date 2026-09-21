"""One function per control-flow shape card 3 must cover."""


def branching(x):
    """if/else: two successors from one block, then a merge."""
    if x > 0:
        label = "positive"
    else:
        label = "negative"
    return label


def looping(items):
    """for loop: a back edge, and an exit edge when the iterator is empty."""
    total = 0
    for item in items:
        total += item
    return total


def while_looping(n):
    """while loop: the test block is the loop head."""
    while n > 0:
        n -= 1
    return n


def guarded(data):
    """try/except/finally: a handler block and a finally block."""
    try:
        value = int(data)
    except ValueError:
        value = 0
    finally:
        seen = True
    return value, seen


def managed(path):
    """with: the body is entered and exited through the context manager."""
    with open(path) as handle:
        return handle.read()


def comprehended(rows):
    """comprehension: an implicit loop, not a flat expression."""
    return [row * 2 for row in rows if row > 0]


def matched(command):
    """match: one branch per case, plus the wildcard."""
    match command:
        case "buy":
            return 1
        case "sell":
            return -1
        case _:
            return 0


def early_return(value):
    """early return: the second statement is unreachable when the guard fires."""
    if value is None:
        return "missing"
    return str(value)
