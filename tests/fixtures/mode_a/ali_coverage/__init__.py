"""Four elements, two intents, one scenario: a coverage figure with no rounding."""


def alpha(value):
    """Confirmed intent; exercised."""
    return value + 1


def beta(value):
    """Confirmed intent; never exercised."""
    return value + 2


def gamma(value):
    """No intent; exercised."""
    return value + 3


def delta(value):
    """No intent; never exercised."""
    return value + 4


def main():
    """Runs alpha and gamma only."""
    return alpha(1), gamma(1)
