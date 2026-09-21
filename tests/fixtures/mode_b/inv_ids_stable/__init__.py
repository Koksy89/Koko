"""Module used to prove IDs survive reformatting.

The test re-indents this file, edits comments and inserts blank lines, then
re-runs the inventory: every ID below must be unchanged, because `make_id` is
structural (module + qualname) and carries no positional information.
"""


def foo():
    """A simple function."""
    pass


def bar():
    """Another function."""
    return 42
