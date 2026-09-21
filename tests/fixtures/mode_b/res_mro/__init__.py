"""Test method resolution order."""
class A:
    def method(self):
        return "A"

class B(A):
    def method(self):
        return "B"

class C(B):
    def method(self):
        return super().method()

c = C()
result = c.method()
