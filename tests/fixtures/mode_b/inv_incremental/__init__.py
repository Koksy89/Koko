"""Module for testing incremental analysis.

Cold run and warm run must be byte-identical. Touching this file without
changing its bytes must not re-analyse it; changing a byte must.
"""


def operation():
    """An operation."""
    return 42
