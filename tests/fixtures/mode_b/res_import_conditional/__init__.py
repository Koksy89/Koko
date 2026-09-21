"""Test conditional import."""
if True:
    import sys
TYPE_CHECKING = False
if TYPE_CHECKING:
    import typing
