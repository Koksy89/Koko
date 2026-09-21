"""Caps and redaction policy for bounded value capture.

The target moves multi-megabyte frames. Capture is bounded *before* a value is
rendered, and anything the caps touch says so explicitly: a truncation that
reads like a complete value is a defect, not a simplification.

Every number here is an int. ``canonical_dumps`` rejects floats, so no cap,
size or ratio anywhere in this card may be one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Pattern, Sequence

__all__ = ["CaptureLimits", "RedactionPolicy", "DEFAULT_LIMITS", "DEFAULT_SENSITIVE_PATTERNS"]


@dataclass(frozen=True, slots=True)
class CaptureLimits:
    """Per-value and per-event caps.

    ``max_event_chars`` is applied to the canonical JSON of an event's whole
    value map, so one event cannot blow the budget by carrying twenty values
    that are each individually under the per-value cap.
    """

    max_repr_chars: int = 512
    """Longest rendered value kept whole."""

    max_event_chars: int = 4096
    """Budget for one event's entire value map, in canonical-JSON characters."""

    max_items: int = 10
    """Longest container kept whole."""

    sample_items: int = 5
    """Items, rows or characters-worth of sample kept from a summarized value."""

    max_string_sample: int = 120
    """Characters of a long string kept as the sample."""

    max_columns: int = 20
    """Columns named in a frame summary before the rest are counted, not named."""

    count_nulls: bool = True
    """Call ``isna()``/``isnull()`` on frame-like values to report null counts.
    Off for values whose accessors must not be touched."""

    max_depth: int = 2
    """Nesting depth rendered inside a container sample."""

    capture_self: bool = False
    """Capture the receiver of a method call. Off by default: every method call
    in the cascade would otherwise render an engine object, which costs more
    than it tells. When off, ``self`` is emitted as an explicit DROPPED capture
    with that reason -- not omitted."""


DEFAULT_LIMITS = CaptureLimits()


DEFAULT_SENSITIVE_PATTERNS: tuple[str, ...] = (
    r"(?i)pass(word|wd|phrase)",
    r"(?i)secret",
    r"(?i)token",
    r"(?i)api[_-]?key",
    r"(?i)access[_-]?key",
    r"(?i)private[_-]?key",
    r"(?i)credential",
    r"(?i)auth(orization)?$",
    r"(?i)session[_-]?id",
    r"(?i)account[_-]?(no|number|id)",
    r"(?i)(^|_)iban($|_)",
    r"(?i)(^|_)ssn($|_)",
    r"(?i)card[_-]?number",
    r"(?i)(^|_)cvv($|_)",
    r"(?i)(^|_)pin($|_)",
)
"""Fallback patterns.

`TARGET_PROFILE.md` leaves "Values to redact" unanswered. Rather than capture
everything in the clear until the owner fills it in, capture redacts these
names by default and the tracer reports that it is running on the fallback set.
"""


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """What is redacted, decided at capture time and never afterwards.

    A value redacted after capture has already been written down once. The only
    place redaction is worth anything is the moment the value is seen, so this
    policy is consulted before a value is rendered at all.
    """

    names: frozenset[str] = frozenset()
    """Exact value names -- an argument, a feature -- always redacted."""

    element_ids: frozenset[str] = frozenset()
    """Elements whose every captured value is redacted."""

    patterns: tuple[str, ...] = DEFAULT_SENSITIVE_PATTERNS
    from_profile: bool = False
    """False when running on the fallback pattern set because the owner has not
    filled in TARGET_PROFILE's redaction list. Reported, never assumed safe."""

    @staticmethod
    def from_owner(
        names: Iterable[str] = (),
        element_ids: Iterable[str] = (),
        patterns: Sequence[str] | None = None,
    ) -> "RedactionPolicy":
        """Build the policy from owner-declared entries."""
        return RedactionPolicy(
            names=frozenset(names),
            element_ids=frozenset(element_ids),
            patterns=tuple(patterns) if patterns is not None else DEFAULT_SENSITIVE_PATTERNS,
            from_profile=True,
        )

    def compiled(self) -> tuple[Pattern[str], ...]:
        return tuple(re.compile(p) for p in self.patterns)

    def reason_for(self, name: str, element_id: str = "") -> str:
        """Return why *name* must be redacted, or "" when it must not."""
        if element_id and element_id in self.element_ids:
            return f"element {element_id} is on the owner's redaction list"
        if name in self.names:
            return f"value name {name!r} is on the owner's redaction list"
        for pattern in self.compiled():
            if pattern.search(name):
                source = "owner" if self.from_profile else "fallback"
                return f"value name {name!r} matches {source} sensitive pattern {pattern.pattern!r}"
        return ""
