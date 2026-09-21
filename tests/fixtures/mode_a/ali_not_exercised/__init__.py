"""One function the scenario runs, one it never reaches."""


def exercised(value):
    """main calls this."""
    return value + 1


def never_called(value):
    """No scenario in this case calls it. It is not thereby correct."""
    return value - 1


def main():
    """Calls only `exercised`."""
    return exercised(1)
