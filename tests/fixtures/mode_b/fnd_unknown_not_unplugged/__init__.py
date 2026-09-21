"""Reachable only through an unresolved call site: UNKNOWN, not unplugged.

`Handler.helper` has no resolved caller. The only thing that could call it is
`getattr(handler, action_name)`, whose name is a parameter -- so the resolver
records an UNKNOWN with `helper` among its candidates. An unplugged-detector
that walks resolved edges only will find `helper` unreached and report it as
dead code, which is wrong: the honest answer is "I could not tell".
"""


class Handler:
    """One method, reachable only by computed name."""

    def helper(self):
        """Candidate of the computed getattr below."""
        return 42


handler = Handler()


def process(action_name):
    """Fetch a method by a name that is only known at runtime."""
    method = getattr(handler, action_name)
    return method()
