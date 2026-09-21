"""Inventories non-Python config/data files: DATA_FILE, and CONFIG_KEY leaves
for the structured formats (JSON, YAML, INI). CSV is DATA_FILE only -- its
cells are rows of data, not addressable wiring keys.

Never executes anything. `json`/`configparser`/`csv` are stdlib and safe to
use on arbitrary text; PyYAML is an optional adapter, tried and skipped
cleanly if absent (core deps are stdlib + networkx only).
"""

from __future__ import annotations

import configparser
import json

from cascade_map.contracts.interfaces import (
    Confidence,
    Element,
    ElementKind,
    Method,
    Provenance,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    config_key_id,
    file_id,
)

from .hashing import sha256_hex, sha256_text

try:
    import yaml  # type: ignore[import-untyped]

    _HAVE_YAML = True
except ImportError:  # pragma: no cover - exercised when PyYAML is absent
    yaml = None  # type: ignore[assignment]
    _HAVE_YAML = False


def _pointer_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _scalar_text(value: object) -> str:
    """A canonical_dumps-safe text form of a leaf value. Floats are formatted
    explicitly here, never passed through as raw floats (contracts constraint 4)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if value is None:
        return "null"
    return str(value)


def parse_data_file(
    kind: str, path_str: str, source_bytes: bytes
) -> tuple[list[Element], list[Unresolved]]:
    file_element = Element(
        id=file_id(path_str),
        kind=ElementKind.DATA_FILE,
        name=path_str.rsplit("/", 1)[-1],
        qualname="",
        module="",
        span=SourceSpan(path=path_str, line=1),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
        content_hash=sha256_hex(source_bytes),
        byte_size=len(source_bytes),
    )
    elements = [file_element]
    unresolved: list[Unresolved] = []

    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        unresolved.append(
            Unresolved(
                id=file_id(path_str),
                reason=UnresolvedReason.DECODE_ERROR,
                span=SourceSpan(path=path_str, line=1),
                description=f"{path_str} is not valid UTF-8: {exc}",
            )
        )
        return elements, unresolved

    if kind == "json":
        _parse_json(text, path_str, file_element.id, elements, unresolved)
    elif kind == "yaml":
        _parse_yaml(text, path_str, file_element.id, elements, unresolved)
    elif kind == "ini":
        _parse_ini(text, path_str, file_element.id, elements, unresolved)
    # csv: DATA_FILE only, no leaf keys.

    return elements, unresolved


def _emit_leaf(
    path_str: str,
    parent_id: str,
    pointer: str,
    value: object,
    elements: list[Element],
) -> None:
    text = _scalar_text(value)
    elements.append(
        Element(
            id=config_key_id(path_str, pointer),
            kind=ElementKind.CONFIG_KEY,
            name=pointer.rsplit("/", 1)[-1] or pointer,
            qualname=pointer,
            module="",
            span=SourceSpan(path=path_str, line=1),
            provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
            content_hash=sha256_text(text),
            signature=text,
            parent_id=parent_id,
        )
    )


def _walk_json_value(value: object, pointer: str, path_str: str, parent_id: str, elements: list[Element]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _walk_json_value(child, f"{pointer}/{_pointer_escape(str(key))}", path_str, parent_id, elements)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            _walk_json_value(child, f"{pointer}/{idx}", path_str, parent_id, elements)
    else:
        _emit_leaf(path_str, parent_id, pointer, value, elements)


def _parse_json(
    text: str, path_str: str, parent_id: str, elements: list[Element], unresolved: list[Unresolved]
) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        unresolved.append(
            Unresolved(
                id=parent_id,
                reason=UnresolvedReason.SYNTAX_ERROR,
                span=SourceSpan(path=path_str, line=exc.lineno, col=exc.colno),
                description=f"invalid JSON: {exc.msg}",
            )
        )
        return
    _walk_json_value(data, "", path_str, parent_id, elements)


def _parse_yaml(
    text: str, path_str: str, parent_id: str, elements: list[Element], unresolved: list[Unresolved]
) -> None:
    if not _HAVE_YAML:
        # Degrades cleanly: DATA_FILE element already emitted, no CONFIG_KEY
        # children without the optional adapter. Not an error.
        return
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError and subclasses
        unresolved.append(
            Unresolved(
                id=parent_id,
                reason=UnresolvedReason.SYNTAX_ERROR,
                span=SourceSpan(path=path_str, line=1),
                description=f"invalid YAML: {exc}",
            )
        )
        return
    if data is not None:
        _walk_json_value(data, "", path_str, parent_id, elements)


def _parse_ini(
    text: str, path_str: str, parent_id: str, elements: list[Element], unresolved: list[Unresolved]
) -> None:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        unresolved.append(
            Unresolved(
                id=parent_id,
                reason=UnresolvedReason.SYNTAX_ERROR,
                span=SourceSpan(path=path_str, line=1),
                description=f"invalid INI: {exc}",
            )
        )
        return
    for section in parser.sections():
        for key, value in parser.items(section):
            pointer = f"/{_pointer_escape(section)}/{_pointer_escape(key)}"
            _emit_leaf(path_str, parent_id, pointer, value, elements)
