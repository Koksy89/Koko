"""Two unrelated classes with an identically named method. Precision trap."""


class ClassA:
    """Unrelated to ClassB."""

    def process(self):
        """ClassA's own process."""
        return "A"


class ClassB:
    """Unrelated to ClassA."""

    def process(self):
        """ClassB's own process."""
        return "B"


def caller():
    """Each call has exactly one possible target."""
    a = ClassA()
    b = ClassB()
    return a.process(), b.process()
