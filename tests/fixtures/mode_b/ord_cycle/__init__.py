"""Recursive cycle."""
def recursive(n):
    if n > 0:
        return recursive(n - 1)
    return 0
