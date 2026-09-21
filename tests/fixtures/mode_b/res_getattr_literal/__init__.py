"""getattr with a literal name: resolvable, but only PROBABLE."""


class Engine:
    """Two methods, one of them fetched by name."""

    def start(self):
        """Fetched by getattr with a literal."""
        return "started"

    def stop(self):
        """Never fetched."""
        return "stopped"


def run():
    """Fetch `start` by its literal name and call it."""
    engine = Engine()
    method = getattr(engine, "start")
    return method()
