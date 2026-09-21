"""One rule class exists; the config names a second one that does not."""


class ThresholdRule:
    """Named by rules.json and present in the target."""

    def evaluate(self, value):
        """Apply the rule."""
        return value > 0
