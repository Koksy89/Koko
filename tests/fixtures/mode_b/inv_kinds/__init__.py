"""Module with various element kinds."""

import sys
from typing import Optional


GLOBAL_VAR = 42


def simple_func(param: int) -> None:
    """A function."""
    x = 10


class MyClass:
    """A class."""

    class_var = 100

    def __init__(self, value: str) -> None:
        """Initialize."""
        self.instance_var = value

    def regular_method(self, x: int) -> str:
        """A regular method."""
        return str(x)

    @property
    def my_property(self) -> int:
        """A property."""
        return self.instance_var


def outer() -> None:
    """Function with a nested function."""
    def inner() -> None:
        pass

    return inner


def with_closure() -> Optional[object]:
    """Create a closure over `y`."""
    y = 5

    def closure() -> int:
        return y

    return closure
