"""An element nobody wrote an intent for. That is not a failure."""


def documented(value):
    """Has a confirmed intent."""
    return value * 2


def undocumented(value):
    """No intent exists for this element, confirmed or proposed."""
    return value + 100


def main():
    """Runs both."""
    return documented(2), undocumented(2)
