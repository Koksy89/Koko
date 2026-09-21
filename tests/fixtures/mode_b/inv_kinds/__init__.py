"""Module with various element kinds."""

import sys
from typing import Optional


GLOBAL_VAR = 42  # ASSIGNMENT


def simple_func(param: int) -> None:  # FUNCTION with PARAMETER
    """A function."""
    x = 10  # local ASSIGNMENT


class MyClass:  # CLASS
    """A class."""

    class_var = 100  # ASSIGNMENT

    def __init__(self, value: str) -> None:  # METHOD with PARAMETER
        """Initialize."""
        self.instance_var = value  # ASSIGNMENT

    def regular_method(self, x: int) -> str:  # METHOD with PARAMETER
        """A regular method."""
        return str(x)

    @property
    def my_property(self) -> int:  # PROPERTY
        """A property."""
        return self.instance_var


def outer() -> None:
    """Function with nested function."""
    def inner() -> None:  # nested FUNCTION
        pass

    return inner


def with_closure() -> callable:
    """Create a closure."""
    y = 5
    def closure() -> int:  # closure FUNCTION
        return y
    return closure
