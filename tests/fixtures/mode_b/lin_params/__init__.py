"""Parameter binding and return flow across a call, including **kwargs."""


def scale(value, factor=2, **options):
    """`offset` arrives inside **options, not as its own parameter."""
    bumped = value * factor
    if "offset" in options:
        bumped = bumped + options["offset"]
    return bumped


def caller(raw):
    """Bind one positional, one keyword, and one that lands in **options."""
    scaled = scale(raw, factor=3, offset=5)
    return scaled
