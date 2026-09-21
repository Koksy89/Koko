"""Test getattr with literal name."""
class Obj:
    def method(self):
        return 42

obj = Obj()
func = getattr(obj, "method")
result = func()
