"""Tests for the binding contracts.

These are the lead's own tests. They exist because thirteen builders implement
against this module, so a defect here is thirteen defects.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import pytest

from cascade_map.contracts import interfaces as I
from cascade_map.contracts._generate_schema import SCHEMA_PATH, render

# ---------------------------------------------------------------------------
# Q6 — confidence composes by weakest link
# ---------------------------------------------------------------------------


def test_combine_takes_the_weakest_input() -> None:
    assert I.combine(I.Confidence.CERTAIN, I.Confidence.HEURISTIC) is I.Confidence.HEURISTIC
    assert I.combine(I.Confidence.RESOLVED, I.Confidence.PROBABLE) is I.Confidence.PROBABLE
    assert I.combine(I.Confidence.CERTAIN, I.Confidence.CERTAIN) is I.Confidence.CERTAIN


def test_combine_of_nothing_claims_nothing() -> None:
    assert I.combine() is I.Confidence.UNKNOWN


def test_unknown_poisons_a_chain() -> None:
    """A guess anywhere in a derivation cannot be laundered into a fact."""
    chain = [I.Confidence.CERTAIN, I.Confidence.RESOLVED, I.Confidence.UNKNOWN]
    assert I.combine(*chain) is I.Confidence.UNKNOWN


def test_combine_is_order_independent() -> None:
    assert I.combine(I.Confidence.HEURISTIC, I.Confidence.CERTAIN) is I.combine(
        I.Confidence.CERTAIN, I.Confidence.HEURISTIC
    )


def test_every_confidence_level_is_ordered() -> None:
    assert set(I._CONFIDENCE_ORDER) == {c.value for c in I.Confidence}


# ---------------------------------------------------------------------------
# Q5 — identity is structural, never positional
# ---------------------------------------------------------------------------


def test_module_and_member_ids() -> None:
    assert I.make_id("strategy.rules") == "strategy.rules"
    assert I.make_id("strategy.rules", "RuleSet.evaluate") == "strategy.rules::RuleSet.evaluate"


def test_first_occurrence_carries_no_ordinal() -> None:
    """Adding a later redefinition must not rename the first one."""
    assert I.make_id("m", "f", 1) == "m::f"
    assert I.make_id("m", "f", 2) == "m::f#2"


def test_namespaced_ids_do_not_collide_with_code_ids() -> None:
    ids = {
        I.make_id("m", "price"),
        I.feature_id("price"),
        I.file_id("config/wiring.json"),
        I.config_key_id("config/wiring.json", "/components/0/class"),
    }
    assert len(ids) == 4


def test_config_key_id_addresses_the_key_not_the_file() -> None:
    key = I.config_key_id("config/wiring.json", "/components/0/class")
    assert key.startswith(I.file_id("config/wiring.json"))
    assert key != I.file_id("config/wiring.json")


# ---------------------------------------------------------------------------
# Q7 — serialization is deterministic by construction
# ---------------------------------------------------------------------------


def test_key_order_does_not_affect_output() -> None:
    assert I.canonical_dumps({"b": 1, "a": 2}) == I.canonical_dumps({"a": 2, "b": 1})


def test_jsonl_is_sorted_by_key() -> None:
    rows = I.canonical_jsonl([{"id": "c"}, {"id": "a"}, {"id": "b"}]).splitlines()
    assert [json.loads(row)["id"] for row in rows] == ["a", "b", "c"]


def test_jsonl_of_nothing_is_empty_not_a_blank_line() -> None:
    assert I.canonical_jsonl([]) == ""


def test_jsonl_ends_with_exactly_one_newline() -> None:
    out = I.canonical_jsonl([{"id": "a"}])
    assert out.endswith("\n") and not out.endswith("\n\n")


@pytest.mark.parametrize("payload", [{"x": 1.5}, {"x": [1.0]}, {"x": {"y": math.pi}}])
def test_floats_are_rejected(payload: dict) -> None:
    """Float repr varies across platforms; a byte-identical guarantee cannot
    survive it. Rejecting is better than emitting something unreproducible."""
    with pytest.raises(TypeError, match="float"):
        I.canonical_dumps(payload)


def test_dataclasses_and_enums_serialize() -> None:
    span = I.SourceSpan(path="a/b.py", line=3)
    prov = I.Provenance(method=I.Method.AST_DIRECT, confidence=I.Confidence.CERTAIN, span=span)
    out = json.loads(I.canonical_dumps(prov))
    assert out["method"] == "AST_DIRECT"
    assert out["confidence"] == "CERTAIN"
    assert out["span"]["path"] == "a/b.py"


def test_sets_serialize_in_sorted_order() -> None:
    assert I.canonical_dumps({"x": {"b", "a"}}) == '{"x":["a","b"]}'


def test_two_runs_are_byte_identical() -> None:
    def build() -> str:
        return I.canonical_jsonl(
            I.Element(
                id=I.make_id("m", name),
                kind=I.ElementKind.FUNCTION,
                name=name,
                qualname=name,
                module="m",
                span=I.SourceSpan(path="m.py", line=index),
                provenance=I.Provenance(
                    method=I.Method.AST_DIRECT, confidence=I.Confidence.CERTAIN
                ),
                content_hash=f"h{index}",
            )
            for index, name in enumerate(["c", "a", "b"])
        )

    assert build() == build()


# ---------------------------------------------------------------------------
# The generated schema must match the dataclasses
# ---------------------------------------------------------------------------


def test_schema_json_is_not_stale() -> None:
    committed = SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == render(), (
        "schema.json is out of date with interfaces.py. Regenerate it: "
        "python -m cascade_map.contracts._generate_schema"
    )


def test_every_exported_dataclass_has_a_schema_def() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    exported = {
        name
        for name in I.__all__
        if isinstance(getattr(I, name), type) and dataclasses.is_dataclass(getattr(I, name))
    }
    assert exported <= set(schema["$defs"])


def test_every_artifact_names_a_known_record() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for path, spec in schema["artifacts"].items():
        assert spec["record"] in schema["$defs"], path


# ---------------------------------------------------------------------------
# Constraint 2 — provenance is mandatory and model text is always labelled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls", [I.Element, I.Edge, I.Finding, I.AlignmentVerdict, I.DocRecord]
)
def test_provenance_is_required_not_optional(cls: type) -> None:
    fields = {f.name: f for f in dataclasses.fields(cls)}
    assert "provenance" in fields
    assert fields["provenance"].default is dataclasses.MISSING


def test_model_proposed_is_a_method_not_a_confidence() -> None:
    """A model may propose; it never grades its own claim."""
    assert I.Method.MODEL_PROPOSED in set(I.Method)
    assert "MODEL" not in {c.value for c in I.Confidence}


def test_runtime_facts_carry_run_and_event_ids() -> None:
    prov = I.Provenance(
        method=I.Method.RUNTIME_OBSERVED,
        confidence=I.Confidence.CERTAIN,
        run_id="run-1",
        event_ids=("e1",),
    )
    assert prov.run_id and prov.event_ids


def test_not_exercised_is_distinct_from_aligned() -> None:
    assert I.Verdict.NOT_EXERCISED is not I.Verdict.ALIGNED
