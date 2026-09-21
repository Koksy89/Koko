"""Module using getattr with computed name."""

import sys


class Helper:
    def method_a(self):
        return "a"

    def method_b(self):
        return "b"


obj = Helper()
method_name = "method_" + sys.argv[1] if len(sys.argv) > 1 else "method_a"
method = getattr(obj, method_name)
result = method()
