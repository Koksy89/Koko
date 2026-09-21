"""Three-level hierarchy: dispatch lands on the nearest override, plus super()."""


class Base:
    """Level 1."""

    def classify(self):
        """Never reached from `dispatch`: Middle and Leaf both override it."""
        return "base"

    def shared(self):
        """Not overridden anywhere, so this *is* what dispatch reaches."""
        return "shared"


class Middle(Base):
    """Level 2."""

    def classify(self):
        """The nearest override above Leaf."""
        return "middle"


class Leaf(Middle):
    """Level 3."""

    def classify(self):
        """Delegates one step up the MRO."""
        return super().classify()


def dispatch():
    """Call through the deepest class."""
    leaf = Leaf()
    return leaf.classify(), leaf.shared()
