"""Imports under TYPE_CHECKING, under a plain `if`, and under `try`."""

from typing import TYPE_CHECKING

USE_FAST = True

if TYPE_CHECKING:
    from .models import Record

if USE_FAST:
    from .fast import encode
else:
    from .slow import encode

try:
    from .optional_ext import boost
except ImportError:
    boost = None
