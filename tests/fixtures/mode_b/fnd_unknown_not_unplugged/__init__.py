"""
Fixture proving that reachable-via-dynamic-call is UNKNOWN, not unplugged.

The function `helper` is reached only through `getattr` with a computed name.
It must be marked reachable (via UNKNOWN edge), not reported as unplugged.
"""


class Handler:
    def helper(self):
        """Reachable only through computed getattr."""
        return 42


handler = Handler()


def process(action_name):
    """Call helper via computed name."""
    method = getattr(handler, action_name)
    return method()
