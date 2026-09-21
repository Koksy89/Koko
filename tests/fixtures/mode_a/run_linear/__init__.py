"""Simple linear execution - known order end to end."""


def step_one():
    """First step."""
    return 1


def step_two(x):
    """Second step."""
    return x + 2


def main():
    """Main entry - linear flow."""
    a = step_one()
    b = step_two(a)
    return b


if __name__ == "__main__":
    result = main()
