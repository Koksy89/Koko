"""Code built at runtime has no static element: its events are UNMAPPED.

`generated` is compiled from a string while the program runs. No static
analysis produced an element for it, so every event it raises maps to nothing.
Those events must be reported as UNMAPPED, with a mapping rate the owner can
read -- never dropped, because they mark exactly where Mode B fell short.
"""

SOURCE = "def generated(value):\n    return value * 3\n"


def build():
    """Compile a function from a string."""
    namespace = {}
    exec(SOURCE, namespace)
    return namespace["generated"]


def main():
    """Call the statically invisible function."""
    generated = build()
    return generated(4)
