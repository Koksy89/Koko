"""Generate schema.json from interfaces.py.

interfaces.py is the single source of truth. schema.json is derived from it so
that non-Python consumers -- card 15's viewer, and anything the owner writes
against the artifacts -- have a machine-readable contract that cannot drift
from the dataclasses.

Run after changing interfaces.py:

    python -m cascade_map.contracts._generate_schema

`tests/test_contracts.py` fails if the committed file is stale.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import types
import typing
from pathlib import Path

from cascade_map.contracts import interfaces

SCHEMA_PATH = Path(__file__).with_name("schema.json")

#: Which artifact file holds which record type, and how each one is sorted.
ARTIFACTS: dict[str, tuple[str, str]] = {
    "elements.jsonl": ("Element", "id"),
    "edges.jsonl": ("Edge", "id"),
    "unresolved.jsonl": ("Unresolved", "id"),
    "cfg_blocks.jsonl": ("CFGBlock", "id"),
    "cfg_edges.jsonl": ("CFGEdge", "id"),
    "order.jsonl": ("OrderNode", "id"),
    "decisions.jsonl": ("DecisionPoint", "id"),
    "lineage.jsonl": ("LineageEdge", "id"),
    "barriers.jsonl": ("Barrier", "id"),
    "slices.jsonl": ("Slice", "id"),
    "findings.jsonl": ("Finding", "id"),
    "changes.jsonl": ("VersionChange", "id"),
    "impacts.jsonl": ("Impact", "id"),
    "records.jsonl": ("DocRecord", "id"),
    "runtime/events.jsonl": ("TraceEvent", "event_id"),
    "runtime/run.json": ("RunRecord", "run_id"),
    "intents.jsonl": ("Intent", "id"),
    "verdicts.jsonl": ("AlignmentVerdict", "id"),
    "narrative.jsonl": ("NarrativeStep", "id"),
}


def _schema_for(annotation: object) -> dict[str, object]:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (types.UnionType, typing.Union):
        inner = [a for a in args if a is not type(None)]
        schema = _schema_for(inner[0])
        return {"anyOf": [schema, {"type": "null"}]}
    if origin in (tuple, list):
        item = args[0] if args else str
        return {"type": "array", "items": _schema_for(item)}
    if origin is dict:
        value = args[1] if len(args) > 1 else str
        return {"type": "object", "additionalProperties": _schema_for(value)}
    if isinstance(annotation, type):
        if issubclass(annotation, enum.StrEnum):
            return {"type": "string", "enum": [member.value for member in annotation]}
        if dataclasses.is_dataclass(annotation):
            return {"$ref": f"#/$defs/{annotation.__name__}"}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is str:
            return {"type": "string"}
    return {}


def _def_for(cls: type) -> dict[str, object]:
    hints = typing.get_type_hints(cls)
    properties: dict[str, object] = {}
    required: list[str] = []
    for field in dataclasses.fields(cls):
        properties[field.name] = _schema_for(hints[field.name])
        has_default = (
            field.default is not dataclasses.MISSING
            or field.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        )
        if not has_default:
            required.append(field.name)
    definition: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        definition["required"] = sorted(required)
    if cls.__doc__:
        definition["description"] = " ".join(cls.__doc__.split())
    return definition


def build() -> dict[str, object]:
    defs: dict[str, object] = {}
    for name in sorted(interfaces.__all__):
        obj = getattr(interfaces, name)
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            defs[name] = _def_for(obj)
        elif isinstance(obj, type) and issubclass(obj, enum.StrEnum):
            defs[name] = {
                "type": "string",
                "enum": [member.value for member in obj],
                "description": " ".join((obj.__doc__ or "").split()),
            }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://cascade-map/schema.json",
        "title": "CASCADE-MAP artifacts",
        "description": (
            "Generated from cascade_map.contracts.interfaces. Do not edit by "
            "hand: run python -m cascade_map.contracts._generate_schema."
        ),
        "schemaVersion": interfaces.SCHEMA_VERSION,
        "artifacts": {
            path: {"record": record, "sortKey": sort_key, "format": "jsonl"
                   if path.endswith(".jsonl") else "json"}
            for path, (record, sort_key) in sorted(ARTIFACTS.items())
        },
        "$defs": defs,
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> int:
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
