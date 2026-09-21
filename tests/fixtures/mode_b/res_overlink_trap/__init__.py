"""Module with same-named methods on unrelated classes."""


class ClassA:
    def process(self):
        """Process in ClassA."""
        return "A"


class ClassB:
    def process(self):
        """Process in ClassB."""
        return "B"


def caller():
    """Call process on different instances."""
    a = ClassA()
    b = ClassB()
    result_a = a.process()
    result_b = b.process()
    return result_a, result_b
