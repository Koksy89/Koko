"""Two confirmed intents on one element: one met, one broken."""


def clamp(value):
    """Should clamp into 0..100. The upper bound is missing."""
    return max(value, 0)


def main():
    """clamp(-5) is 0 (lower bound holds); clamp(500) is 500 (upper bound fails)."""
    return clamp(-5), clamp(500)
