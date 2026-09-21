"""Test registry dict pattern."""
def handler_a():
    return "A"

def handler_b():
    return "B"

registry = {
    "a": handler_a,
    "b": handler_b,
}

func = registry.get("a")
if func:
    result = func()
