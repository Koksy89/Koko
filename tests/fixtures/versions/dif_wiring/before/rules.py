"""Two rule classes. Both exist in both versions; only the wiring moves."""


class AlphaRule:
    """Wired in the before version."""

    def evaluate(self, value):
        """Apply."""
        return value > 0


class BetaRule:
    """Wired in the after version."""

    def evaluate(self, value):
        """Apply."""
        return value > 100
