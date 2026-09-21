"""The components a config file wires together by name."""


class ScoreComponent:
    """Named by wiring.json."""

    def process(self, value):
        """Do the work."""
        return value


class UnwiredComponent:
    """Never named by any config key."""

    def process(self, value):
        """Do the work."""
        return value
