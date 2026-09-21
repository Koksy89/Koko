"""getattr with a name built at runtime: UNKNOWN with candidates, never a guess."""

import sys


class Helper:
    """Two same-prefixed methods; which one runs is decided at runtime."""

    def method_a(self):
        """Candidate."""
        return "a"

    def method_b(self):
        """Candidate."""
        return "b"


def run():
    """Build the attribute name from argv, then fetch it."""
    obj = Helper()
    suffix = sys.argv[1] if len(sys.argv) > 1 else "a"
    method_name = "method_" + suffix
    method = getattr(obj, method_name)
    return method()
