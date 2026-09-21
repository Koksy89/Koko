"""An edge the static graph predicts, that this scenario never takes."""


def fallback_path(value):
    """Statically reachable from dispatch; never called in this scenario."""
    return "fallback"


def primary_path(value):
    """The arm this scenario actually takes."""
    return "primary"


def dispatch(value, use_fallback=False):
    """Both arms are live to static analysis."""
    if use_fallback:
        return fallback_path(value)
    return primary_path(value)


def main():
    """Never passes use_fallback=True."""
    return dispatch(1)
