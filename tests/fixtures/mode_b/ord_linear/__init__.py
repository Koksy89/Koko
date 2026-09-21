"""Module with linear execution order."""


def first():
    return 1


def second(x):
    return first() + x


def third():
    return second(2)


def main():
    return third()
