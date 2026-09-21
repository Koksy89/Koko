"""Bounded, redacting value capture.

Three rules govern every function here.

1. **Nothing is silently truncated.** A capture that is not ``FULL`` carries a
   ``CaptureStatus``, an ``original_size``, a ``reason``, and a ``repr_text``
   that *begins* with the status in angle brackets. A reader can never mistake
   a summary for the whole value.
2. **Redaction happens before rendering.** A sensitive value is never turned
   into a string at all, so it cannot leak through a summary, a length or an
   exception message.
3. **Capture never raises.** A value whose ``repr`` or accessors blow up
   becomes a ``DROPPED`` capture naming the exception. Losing an event because
   one argument misbehaved would lose the evidence the run exists to produce.
"""

from __future__ import annotations

from typing import Any, Mapping

from cascade_map.contracts.interfaces import CaptureStatus, ValueCapture, canonical_dumps

from .limits import DEFAULT_LIMITS, CaptureLimits, RedactionPolicy

__all__ = [
    "capture_value",
    "capture_values",
    "dropped",
    "MISSING",
]


class _Missing:
    """A value the tracer could not obtain at all."""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<missing>"


MISSING = _Missing()

_SCALARS = (bool, int, type(None))
_CONTAINERS = (list, tuple, set, frozenset, dict)


def dropped(reason: str, type_name: str = "", original_size: int = 0) -> ValueCapture:
    """A value that was not captured, and why."""
    return ValueCapture(
        status=CaptureStatus.DROPPED,
        repr_text=f"<DROPPED: {reason}>",
        type_name=type_name,
        original_size=original_size,
        reason=reason,
    )


def capture_value(
    name: str,
    value: Any,
    *,
    limits: CaptureLimits = DEFAULT_LIMITS,
    policy: RedactionPolicy | None = None,
    element_id: str = "",
) -> ValueCapture:
    """Capture one value under the caps, redacting first."""
    type_name = _type_name(value)
    if value is MISSING:
        return dropped("value not reachable from the frame", type_name)

    policy = policy if policy is not None else RedactionPolicy()
    redaction = policy.reason_for(name, element_id)
    if redaction:
        # Deliberately no repr, no length, no shape: the value is not rendered.
        return ValueCapture(
            status=CaptureStatus.REDACTED,
            repr_text="<REDACTED>",
            type_name=type_name,
            original_size=0,
            reason=(
                f"{redaction}; the value was not inspected, so no size was measured"
            ),
        )

    try:
        return _capture(value, type_name, limits)
    except BaseException as exc:  # noqa: BLE001 - capture must never raise
        return dropped(f"capture raised {type(exc).__name__}", type_name)


def capture_values(
    values: Mapping[str, Any],
    *,
    limits: CaptureLimits = DEFAULT_LIMITS,
    policy: RedactionPolicy | None = None,
    element_id: str = "",
) -> dict[str, ValueCapture]:
    """Capture a whole event's values under the per-event budget.

    Names are processed in sorted order so that which value exhausts the budget
    is a property of the data, not of dict ordering. Values past the budget are
    ``DROPPED`` with their measured size -- never omitted.
    """
    captured: dict[str, ValueCapture] = {}
    spent = 0
    exhausted = False
    for key in sorted(values):
        capture = capture_value(
            key, values[key], limits=limits, policy=policy, element_id=element_id
        )
        cost = len(canonical_dumps(capture)) + len(key) + 3
        if exhausted or spent + cost > limits.max_event_chars:
            exhausted = True
            captured[key] = dropped(
                f"per-event capture budget of {limits.max_event_chars} characters exhausted "
                f"after {spent} characters; this value needed {cost}",
                capture.type_name,
                original_size=cost,
            )
            continue
        spent += cost
        captured[key] = capture
    return captured


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _type_name(value: Any) -> str:
    try:
        return type(value).__qualname__
    except BaseException:  # noqa: BLE001 # pragma: no cover
        return "<unknown>"


def _capture(value: Any, type_name: str, limits: CaptureLimits) -> ValueCapture:
    if isinstance(value, _SCALARS):
        return ValueCapture(CaptureStatus.FULL, repr(value), type_name)
    if isinstance(value, float):
        # Rendered as a string: canonical_dumps rejects float payloads outright.
        return ValueCapture(CaptureStatus.FULL, repr(value), type_name)
    if isinstance(value, str):
        return _capture_string(value, type_name, limits)
    if isinstance(value, (bytes, bytearray)):
        return _capture_bytes(value, type_name, limits)
    if _is_frame_like(value):
        return _capture_frame(value, type_name, limits)
    if _is_array_like(value):
        return _capture_array(value, type_name, limits)
    if isinstance(value, _CONTAINERS):
        return _capture_container(value, type_name, limits)
    return _capture_object(value, type_name, limits)


def _capture_string(value: str, type_name: str, limits: CaptureLimits) -> ValueCapture:
    rendered = repr(value)
    if len(rendered) <= limits.max_repr_chars:
        return ValueCapture(CaptureStatus.FULL, rendered, type_name)
    sample = repr(value[: limits.max_string_sample])
    reason = (
        f"string of {len(value)} characters exceeds max_repr_chars="
        f"{limits.max_repr_chars}; first {limits.max_string_sample} characters kept"
    )
    return ValueCapture(
        status=CaptureStatus.SUMMARIZED,
        repr_text=f"<SUMMARIZED: str len={len(value)} head={sample}>",
        type_name=type_name,
        shape=f"len={len(value)}",
        original_size=len(value),
        reason=f"{reason}; original_size is characters",
    )


def _capture_bytes(value: bytes | bytearray, type_name: str, limits: CaptureLimits) -> ValueCapture:
    if len(value) * 4 <= limits.max_repr_chars:
        return ValueCapture(CaptureStatus.FULL, repr(bytes(value)), type_name)
    head = bytes(value[: limits.sample_items]).hex()
    return ValueCapture(
        status=CaptureStatus.SUMMARIZED,
        repr_text=f"<SUMMARIZED: {type_name} len={len(value)} head_hex={head}>",
        type_name=type_name,
        shape=f"len={len(value)}",
        original_size=len(value),
        reason=(
            f"binary value of {len(value)} bytes exceeds the render cap; "
            f"first {limits.sample_items} bytes kept as hex; original_size is bytes"
        ),
    )


def _capture_container(value: Any, type_name: str, limits: CaptureLimits) -> ValueCapture:
    count = len(value)
    if count <= limits.max_items:
        rendered = _render(value, limits, limits.max_depth)
        if len(rendered) <= limits.max_repr_chars:
            return ValueCapture(CaptureStatus.FULL, rendered, type_name, shape=f"len={count}")
    sample = _render(value, limits, limits.max_depth, cap=limits.sample_items)
    if len(sample) > limits.max_repr_chars:
        sample = sample[: limits.max_repr_chars] + "..."
    return ValueCapture(
        status=CaptureStatus.SUMMARIZED,
        repr_text=f"<SUMMARIZED: {type_name} len={count} sample={sample}>",
        type_name=type_name,
        shape=f"len={count}",
        original_size=count,
        reason=(
            f"container of {count} items exceeds max_items={limits.max_items} or "
            f"max_repr_chars={limits.max_repr_chars}; first {limits.sample_items} items "
            "kept; original_size is items"
        ),
    )


def _capture_frame(value: Any, type_name: str, limits: CaptureLimits) -> ValueCapture:
    shape = _attr(value, "shape")
    rows, cols = _rows_cols(shape)
    columns = _column_dtypes(value, limits)
    nulls = _null_counts(value, limits) if limits.count_nulls else "not counted"
    sample = _frame_sample(value, limits)
    size, unit = _frame_size(value, rows, cols)
    return ValueCapture(
        status=CaptureStatus.SUMMARIZED,
        repr_text=(
            f"<SUMMARIZED: {type_name} rows={rows} cols={cols} columns={columns} "
            f"nulls={nulls} head={sample}>"
        ),
        type_name=type_name,
        shape=_shape_text(shape),
        original_size=size,
        reason=(
            "frame-like values are always summarized, never rendered whole; "
            f"first {limits.sample_items} rows sampled; original_size is {unit}"
        ),
    )


def _capture_array(value: Any, type_name: str, limits: CaptureLimits) -> ValueCapture:
    shape = _attr(value, "shape")
    dtype = _text(_attr(value, "dtype"))
    sample = _render(value, limits, 1, cap=limits.sample_items)
    if len(sample) > limits.max_repr_chars:
        sample = sample[: limits.max_repr_chars] + "..."
    size = _int(_attr(value, "nbytes"))
    unit = "bytes"
    if size == 0:
        size = _element_count(shape)
        unit = "elements"
    return ValueCapture(
        status=CaptureStatus.SUMMARIZED,
        repr_text=(
            f"<SUMMARIZED: {type_name} shape={_shape_text(shape)} dtype={dtype} sample={sample}>"
        ),
        type_name=type_name,
        shape=_shape_text(shape),
        original_size=size,
        reason=(
            "array-like values are always summarized, never rendered whole; "
            f"first {limits.sample_items} elements sampled; original_size is {unit}"
        ),
    )


def _capture_object(value: Any, type_name: str, limits: CaptureLimits) -> ValueCapture:
    rendered = repr(value)
    if len(rendered) <= limits.max_repr_chars:
        return ValueCapture(CaptureStatus.FULL, rendered, type_name)
    head = rendered[: limits.max_string_sample]
    return ValueCapture(
        status=CaptureStatus.SUMMARIZED,
        repr_text=f"<SUMMARIZED: {type_name} repr_len={len(rendered)} head={head!r}>",
        type_name=type_name,
        original_size=len(rendered),
        reason=(
            f"repr of {len(rendered)} characters exceeds max_repr_chars="
            f"{limits.max_repr_chars}; original_size is characters of repr"
        ),
    )


def _render(value: Any, limits: CaptureLimits, depth: int, cap: int | None = None) -> str:
    """Deterministic bounded rendering. Sets are sorted; dicts keep their order."""
    if isinstance(value, _SCALARS) or isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if len(value) <= limits.max_string_sample:
            return repr(value)
        return f"{value[: limits.max_string_sample]!r}...+{len(value) - limits.max_string_sample}"
    if isinstance(value, (bytes, bytearray)):
        return f"<{_type_name(value)} len={len(value)}>"
    if depth <= 0:
        return f"<{_type_name(value)}>"
    limit = cap if cap is not None else limits.max_items
    if isinstance(value, dict):
        items = list(value.items())[:limit]
        body = ",".join(
            f"{_render(k, limits, depth - 1)}:{_render(v, limits, depth - 1)}" for k, v in items
        )
        more = len(value) - len(items)
        return "{" + body + (f",...+{more}" if more > 0 else "") + "}"
    if isinstance(value, (set, frozenset)):
        ordered = sorted((_render(item, limits, depth - 1) for item in value))[:limit]
        more = len(value) - len(ordered)
        return "{" + ",".join(ordered) + (f",...+{more}" if more > 0 else "") + "}"
    if isinstance(value, (list, tuple)):
        items = list(value)[:limit]
        body = ",".join(_render(item, limits, depth - 1) for item in items)
        more = len(value) - len(items)
        opener, closer = ("[", "]") if isinstance(value, list) else ("(", ")")
        return opener + body + (f",...+{more}" if more > 0 else "") + closer
    sliced = _slice(value, limit)
    if sliced is not None:
        return _render(list(sliced), limits, depth, cap=limit)
    return f"<{_type_name(value)}>"


# ---- duck-typed inspection; every accessor is guarded ----------------------


def _is_frame_like(value: Any) -> bool:
    return all(_has(value, name) for name in ("shape", "columns", "dtypes"))


def _is_array_like(value: Any) -> bool:
    return _has(value, "shape") and _has(value, "dtype")


def _has(value: Any, name: str) -> bool:
    try:
        return hasattr(value, name)
    except BaseException:  # noqa: BLE001
        return False


def _attr(value: Any, name: str) -> Any:
    try:
        return getattr(value, name)
    except BaseException:  # noqa: BLE001
        return None


def _text(value: Any) -> str:
    try:
        return str(value)
    except BaseException:  # noqa: BLE001
        return "<unreadable>"


def _int(value: Any) -> int:
    try:
        return int(value)
    except BaseException:  # noqa: BLE001
        return 0


def _slice(value: Any, limit: int) -> Any:
    try:
        return value[:limit]
    except BaseException:  # noqa: BLE001
        return None


def _shape_text(shape: Any) -> str:
    if shape is None:
        return ""
    try:
        return "(" + ", ".join(str(int(dim)) for dim in shape) + ")"
    except BaseException:  # noqa: BLE001
        return _text(shape)


def _rows_cols(shape: Any) -> tuple[int, int]:
    try:
        dims = [int(dim) for dim in shape]
    except BaseException:  # noqa: BLE001
        return (0, 0)
    rows = dims[0] if dims else 0
    cols = dims[1] if len(dims) > 1 else 1
    return (rows, cols)


def _element_count(shape: Any) -> int:
    try:
        total = 1
        for dim in shape:
            total *= int(dim)
        return total
    except BaseException:  # noqa: BLE001
        return 0


def _column_dtypes(value: Any, limits: CaptureLimits) -> str:
    columns = _attr(value, "columns")
    dtypes = _attr(value, "dtypes")
    try:
        names = [str(name) for name in columns]
    except BaseException:  # noqa: BLE001
        return "<unreadable>"
    mapping: dict[str, str] = {}
    try:
        mapping = {str(k): _text(v) for k, v in dict(dtypes).items()}
    except BaseException:  # noqa: BLE001
        mapping = {}
    shown = names[: limits.max_columns]
    body = ",".join(f"{name}:{mapping.get(name, '?')}" for name in shown)
    more = len(names) - len(shown)
    return "[" + body + (f",...+{more}" if more > 0 else "") + "]"


def _null_counts(value: Any, limits: CaptureLimits) -> str:
    for accessor in ("isna", "isnull"):
        method = _attr(value, accessor)
        if method is None:
            continue
        try:
            counts = dict(method().sum())
        except BaseException:  # noqa: BLE001
            continue
        shown = list(counts.items())[: limits.max_columns]
        body = ",".join(f"{str(k)}:{_int(v)}" for k, v in shown)
        more = len(counts) - len(shown)
        return "{" + body + (f",...+{more}" if more > 0 else "") + "}"
    return "unavailable"


def _frame_sample(value: Any, limits: CaptureLimits) -> str:
    head = _attr(value, "head")
    rows: Any = None
    if head is not None:
        try:
            rows = head(limits.sample_items)
        except BaseException:  # noqa: BLE001
            rows = None
    if rows is None:
        return "unavailable"
    records = _attr(rows, "to_dict")
    if records is not None:
        try:
            return _render(records("records"), limits, 2, cap=limits.sample_items)
        except BaseException:  # noqa: BLE001
            pass
    return _render(rows, limits, 2, cap=limits.sample_items)


def _frame_size(value: Any, rows: int, cols: int) -> tuple[int, str]:
    usage = _attr(value, "memory_usage")
    if usage is not None:
        try:
            return (_int(usage(deep=True).sum()), "bytes")
        except BaseException:  # noqa: BLE001
            pass
    nbytes = _attr(value, "nbytes")
    if nbytes is not None:
        size = _int(nbytes)
        if size:
            return (size, "bytes")
    return (rows * cols, "cells")
