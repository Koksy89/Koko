"""Two confirmed intents that cannot both be satisfied by any implementation."""


def round_trip(value):
    """No implementation can satisfy both registered intents at once."""
    return value * 2


def main():
    """round_trip(3) returns 6."""
    return round_trip(3)
