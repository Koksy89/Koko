"""Test function-local import."""
def func():
    import json
    return json.dumps({})
