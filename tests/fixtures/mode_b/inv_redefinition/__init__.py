"""Module with redefined names."""


def func():
    """First definition."""
    return 1


if True:
    def func():
        """Redefined in conditional."""
        return 2
