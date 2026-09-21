"""Tests for card 8 -- the fixture corpus itself.

The corpus is the fixed point every other card is graded against, so a wrong
expectation file silently corrupts another card's verdict. These tests check
the corpus the way the corpus checks everything else:

* **Coverage.** Every case ID `FIXTURES.md` specifies exists, with a program
  and an expectation. Extras are allowed and are named, not rounded away.
* **Mechanical correctness.** Every span-bearing record in every
  `expected.json` is resolved against the fixture source with `ast` and must
  agree -- the check that caught 14 wrong line numbers across 212 records in
  each of the two previous rounds. The count of records checked is asserted,
  so "0 mismatches" cannot come from checking nothing.
* **Discriminating power.** An expectation that would pass against almost any
  implementation is worthless. A case whose purpose is proving a `FindingKind`
  fires must require exactly that finding; a case must say more than "a module
  element exists".
* **Safety.** Nothing here imports, executes or `exec`s any fixture. The
  sentinel is proved to be armed by reading its AST, never by running it --
  fixtures may be executed only by harness and tracer tests.

Nothing in this file writes to `tests/fixtures/`.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from cascade_map.contracts.interfaces import FindingKind, canonical_dumps

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
MODE_A = FIXTURES / "mode_a"
MODE_B = FIXTURES / "mode_b"
VERSIONS = FIXTURES / "versions"
SENTINEL = FIXTURES / "sentinel"

SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")

#: `evt_` plus a zero-padded 8-digit ordinal -- see TraceEvent.event_id.
_EVENT_ID = re.compile(r"evt_\d{8}")


def _load_tool(name: str):
    """Import one of the corpus's own tools by path.

    `tests/fixtures/_regenerate_spans.py` and `_check_spans.py` are tooling,
    not fixtures: they read the corpus and never execute any part of it.
    """
    # Bytecode writing is suppressed for the duration: a __pycache__ written
    # into tests/fixtures/ is a change to the corpus, and card 11's harness
    # suite hashes the whole tree before and after it runs. This has already
    # cost the build once.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, FIXTURES / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


# ---------------------------------------------------------------------------
# The specification: every case ID FIXTURES.md names
# ---------------------------------------------------------------------------

INVENTORY_CASES = (
    "inv_blob",
    "inv_empty",
    "inv_ids_stable",
    "inv_incremental",
    "inv_kinds",
    "inv_non_utf8",
    "inv_redefinition",
    "inv_syntax_error",
)

RESOLUTION_CASES = (
    "res_config_dangling",
    "res_config_wiring",
    "res_decorator",
    "res_getattr_computed",
    "res_getattr_literal",
    "res_import_absolute",
    "res_import_conditional",
    "res_import_cycle",
    "res_import_local",
    "res_import_relative",
    "res_import_star",
    "res_importlib",
    "res_mro",
    "res_overlink_trap",
    "res_reexport",
    "res_registry_decorator",
    "res_registry_dict",
)

CASCADE_CASES = (
    "cfg_shapes",
    "cfg_shortcircuit",
    "dec_guard_clause",
    "dec_rule_cascade",
    "dec_sink",
    "dec_uncertain_edge",
    "ord_branching",
    "ord_cycle",
    "ord_linear",
    "ord_unordered",
)

LINEAGE_CASES = (
    "lin_assign_chain",
    "lin_barrier",
    "lin_closure",
    "lin_container",
    "lin_dataframe",
    "lin_feature_named",
    "lin_params",
    "lin_slice_backward",
    "lin_slice_forward",
)

#: One case per FindingKind. These eight are implicit in FIXTURES.md's
#: "One case per `FindingKind`, plus:" -- the case ID is the kind, lowercased.
FINDING_KIND_CASES = {
    FindingKind.DANGLING_CONFIG_REFERENCE: "fnd_dangling_config_reference",
    FindingKind.DEAD_BRANCH: "fnd_dead_branch",
    FindingKind.DECISION_IRRELEVANT: "fnd_decision_irrelevant",
    FindingKind.DUPLICATED_LOGIC: "fnd_duplicated_logic",
    FindingKind.ORPHANED_CONFIG_ELEMENT: "fnd_orphaned_config_element",
    FindingKind.SHADOWED_DEFINITION: "fnd_shadowed_definition",
    FindingKind.UNCONSUMED_FEATURE: "fnd_unconsumed_feature",
    FindingKind.UNREACHABLE_ELEMENT: "fnd_unreachable_element",
}

FINDING_CASES = tuple(sorted(FINDING_KIND_CASES.values())) + (
    "fnd_no_false_positive",
    "fnd_unknown_not_unplugged",
)

DIFF_CASES = (
    "dif_added_removed",
    "dif_format_only",
    "dif_impact_rank",
    "dif_move",
    "dif_rename",
    "dif_signature",
    "dif_symmetric",
    "dif_wiring",
)

RUN_CASES = (
    "run_branching",
    "run_contradiction",
    "run_exception",
    "run_large_frame",
    "run_linear",
    "run_nondeterministic",
    "run_redaction",
    "run_replay",
    "run_unmapped",
    "run_values",
)

ADVERSARIAL_CASES = (
    "adv_dns",
    "adv_network",
    "adv_refuse_start",
    "adv_stale_graph",
    "adv_subprocess",
    "adv_undeclared_client",
    "adv_write_escape",
)

ALIGNMENT_NARRATIVE_CASES = (
    "ali_aligned",
    "ali_misaligned",
    "ali_no_intent",
    "ali_not_exercised",
    "ali_proposed_not_binding",
    "nar_absence",
    "nar_anchored",
    "nar_deterministic",
    "nar_loop_summary",
)

#: Present on disk, useful, and not specified by FIXTURES.md. Named rather
#: than folded into the specified count.
UNSPECIFIED_EXTRAS = (
    "ali_conflicting",
    "ali_coverage",
    "ali_multiple_intents",
    "ali_proposed",
    "nar_drill_down",
    "nar_skipped_steps",
    "nar_summary_report",
)

SPECIFIED_MODE_B = INVENTORY_CASES + RESOLUTION_CASES + CASCADE_CASES + LINEAGE_CASES + FINDING_CASES
SPECIFIED_MODE_A = RUN_CASES + ADVERSARIAL_CASES + ALIGNMENT_NARRATIVE_CASES
SPECIFIED_TOTAL = len(SPECIFIED_MODE_B) + len(SPECIFIED_MODE_A) + len(DIFF_CASES)


def case_dir(case_id: str) -> Path:
    if case_id in SPECIFIED_MODE_B:
        return MODE_B / case_id
    if case_id in DIFF_CASES:
        return VERSIONS / case_id
    return MODE_A / case_id


def expectation(case_id: str) -> dict[str, Any]:
    return json.loads((case_dir(case_id) / "expected.json").read_text())


def all_case_dirs() -> list[Path]:
    return sorted(p.parent for p in FIXTURES.rglob("expected.json"))


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_the_specified_case_count_is_eighty_eight() -> None:
    """FIXTURES.md names 80 cases in its tables and implies 8 more, one per
    FindingKind. Pinned so a case cannot be dropped from the lists above
    without this failing."""
    assert len(SPECIFIED_MODE_B) == 54
    assert len(SPECIFIED_MODE_A) == 26
    assert len(DIFF_CASES) == 8
    assert SPECIFIED_TOTAL == 88


@pytest.mark.parametrize("case_id", SPECIFIED_MODE_B + SPECIFIED_MODE_A + DIFF_CASES)
def test_every_specified_case_exists(case_id: str) -> None:
    directory = case_dir(case_id)
    assert directory.is_dir(), f"{case_id}: no directory at {directory}"
    assert (directory / "expected.json").is_file(), f"{case_id}: no expected.json"
    assert (directory / "spans.json").is_file(), f"{case_id}: no spans.json"


@pytest.mark.parametrize("case_id", SPECIFIED_MODE_B + SPECIFIED_MODE_A)
def test_every_specified_case_has_a_program(case_id: str) -> None:
    sources = [p for p in case_dir(case_id).rglob("*.py") if p.name != "expected.json"]
    assert sources, f"{case_id}: an expectation with no program tests nothing"


@pytest.mark.parametrize("case_id", DIFF_CASES)
def test_every_version_case_has_both_versions(case_id: str) -> None:
    directory = case_dir(case_id)
    for side in ("before", "after"):
        sources = sorted((directory / side).glob("*.py"))
        assert sources, f"{case_id}/{side}: no modules"


def test_unspecified_extras_are_exactly_the_seven_named() -> None:
    """The corpus carries seven cases FIXTURES.md does not specify. They are
    kept and named here; an eighth appearing silently is a defect."""
    on_disk = {p.name for p in all_case_dirs()}
    specified = set(SPECIFIED_MODE_B) | set(SPECIFIED_MODE_A) | set(DIFF_CASES)
    extras = on_disk - specified - {"sentinel"}
    assert sorted(extras) == sorted(UNSPECIFIED_EXTRAS)


def test_the_corpus_holds_ninety_six_cases() -> None:
    """88 specified + 7 unspecified extras + the sentinel."""
    assert len(all_case_dirs()) == 96


def test_the_corpus_contains_no_build_artifacts() -> None:
    """A `__pycache__` under tests/fixtures/ is a change to the fixed point
    every card is graded against, and card 11's suite hashes the tree before
    and after it runs. It has already cost this build once."""
    artifacts = [
        p.relative_to(FIXTURES).as_posix()
        for p in FIXTURES.rglob("*")
        if p.name == "__pycache__" or p.suffix in {".pyc", ".pyo"}
    ]
    assert artifacts == [], f"build artifacts in the corpus: {artifacts}"


# ---------------------------------------------------------------------------
# Mechanical correctness: every line number, against the AST
# ---------------------------------------------------------------------------


def test_every_span_bearing_record_agrees_with_the_source() -> None:
    """The check both previous rounds failed.

    Both the number of records checked and the number of mismatches are
    asserted: "0 mismatches" over 12 records and over 700 are not the same
    claim, and only the second is worth anything.
    """
    checker = _load_tool("_check_spans")
    total_checked = 0
    problems: list[str] = []
    cases = sorted(FIXTURES.rglob("spans.json"))
    assert len(cases) == 96, "every case must carry its span anchors"
    for spans_file in cases:
        checked, found = checker.check_case(spans_file.parent)
        total_checked += checked
        problems.extend(found)
        problems.extend(checker.audit_case(spans_file.parent))

    assert total_checked >= 700, f"only {total_checked} span-bearing records found"
    assert problems == [], "\n".join(problems[:40])


def test_the_span_generator_is_idempotent() -> None:
    """Running it a second time must change nothing, so a regenerated corpus
    is byte-identical to the committed one."""
    generator = _load_tool("_regenerate_spans")
    assert generator.main(["--check"]) == 0


def test_two_regenerations_are_byte_identical() -> None:
    """Constraint 4, applied to the corpus itself."""

    def digest() -> str:
        sha = hashlib.sha256()
        for path in sorted(FIXTURES.rglob("expected.json")):
            sha.update(path.read_bytes())
        return sha.hexdigest()

    first = digest()
    second = digest()
    assert first == second
    generator = _load_tool("_regenerate_spans")
    assert generator.main(["--check"]) == 0
    assert digest() == first


def test_every_expectation_is_canonically_serializable() -> None:
    """No floats, no unserializable values: constraint 4 rejects them."""
    cases = all_case_dirs()
    assert len(cases) == 96
    for directory in cases:
        payload = json.loads((directory / "expected.json").read_text())
        canonical_dumps(payload)


# ---------------------------------------------------------------------------
# Discriminating power
# ---------------------------------------------------------------------------

#: Keys that make an expectation say something an implementation could fail.
_DISCRIMINATING_KEYS = frozenset(
    {
        "alias_resolves_to",
        "allowed_outcomes_per_route",
        "barriers",
        "blocked_attempts_required",
        "branch_sequence",
        "cfg_requirements",
        "changes",
        "checkable_claims",
        "contradictions",
        "coverage_counts",
        "cycle_count_exact",
        "decisions",
        "edges",
        "element_count",
        "event_count_exact",
        "events",
        "expected_calls",
        "expected_capture",
        "expected_captures",
        "expected_exceptions",
        "expected_nesting",
        "expected_step_elements_in_order",
        "expected_unmapped",
        "feature_nodes_exact",
        "finding_count_exact",
        "findings",
        "forbidden_order_kinds_over_elements",
        "intents",
        "iterations_exact",
        "lineage",
        "mapping_report",
        "marker_must_be_absent_after_the_run",
        "marker_must_be_absent_after_any_static_run",
        "must_not_be_in_slice",
        "must_not_contain_changes",
        "must_not_contain_edges",
        "must_not_contain_findings",
        "must_not_contain_lineage",
        "must_produce_kinds",
        "nondeterminism",
        "order",
        "ordered_ids_for_name_func",
        "reachability",
        "required_run_record",
        "skipped_steps_must_be_reported",
        "slices",
        "stage_order",
        "static_edges_predicted",
        "text_must_not_contain",
        "two_renders_must_be_byte_identical",
        "unresolved",
        "verdicts",
    }
)


def test_no_expectation_is_boilerplate() -> None:
    """An expectation that carries only a module element and empty lists would
    pass against almost any implementation, including one that returns
    nothing. Every case must require something specific.

    `unresolved` counts only when non-empty; an empty list is a real claim
    *only* next to something that could have filled it, which the other keys
    supply.
    """
    cases = all_case_dirs()
    assert len(cases) == 96
    weak: list[str] = []
    for directory in cases:
        payload = json.loads((directory / "expected.json").read_text())
        elements = payload.get("elements", []) or payload.get("before_elements", [])
        discriminating = [
            key
            for key in _DISCRIMINATING_KEYS & payload.keys()
            if payload[key] not in ([], {}, "", None, False)
        ]
        if len(elements) <= 1 and not discriminating:
            weak.append(f"{directory.name}: {sorted(payload)}")
    assert weak == [], "boilerplate expectations:\n" + "\n".join(weak)


@pytest.mark.parametrize("kind,case_id", sorted(FINDING_KIND_CASES.items()))
def test_each_finding_kind_case_requires_exactly_that_finding(
    kind: FindingKind, case_id: str
) -> None:
    """The eight cases that were fabricated in round 2 -- a lone docstring and
    `findings: []`. A case whose entire purpose is proving a FindingKind fires
    cannot expect zero findings."""
    payload = expectation(case_id)
    findings = payload.get("findings", [])
    assert len(findings) == 1, f"{case_id}: expected exactly one finding, got {len(findings)}"
    finding = findings[0]
    assert finding["kind"] == str(kind)
    assert payload.get("finding_count_exact") == 1
    assert finding["evidence_ids"], "a finding with an empty evidence chain does not ship"
    assert finding["summary"].strip()
    assert finding["hint"].strip()
    assert finding["span"]["path"].endswith((".py", ".json"))
    assert payload.get("must_not_contain_findings"), (
        f"{case_id}: the findings that must NOT fire are half the expectation"
    )


@pytest.mark.parametrize("kind,case_id", sorted(FINDING_KIND_CASES.items()))
def test_each_finding_kind_case_has_a_real_program(kind: FindingKind, case_id: str) -> None:
    """Round 2's eight stubs were a single docstring with no code."""
    source = (case_dir(case_id) / "__init__.py").read_text()
    tree = ast.parse(source)
    statements = [n for n in tree.body if not _is_docstring(n)]
    assert statements, f"{case_id}: the program is a bare docstring"
    definitions = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert definitions, f"{case_id}: no function or class to exhibit {kind}"


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def test_dec_uncertain_edge_requires_the_heuristic_edge() -> None:
    """The case is named for a HEURISTIC edge. An expectation with `edges: []`
    does not test it."""
    payload = expectation("dec_uncertain_edge")
    heuristic = [
        e for e in payload["edges"] if e["provenance"]["confidence"] == "HEURISTIC"
    ]
    assert len(heuristic) == 1
    edge = heuristic[0]
    assert edge["target_id"] == "dec_uncertain_edge::momentum_rule"
    assert edge["provenance"]["method"] == "DECORATOR_REGISTRATION"

    reach = {r["element_id"]: r for r in payload["reachability"]}
    verdict = reach["dec_uncertain_edge::momentum_rule"]
    assert verdict["state"] == "REACHES_SINK", "an uncertain edge must not be pruned"
    assert verdict["provenance"]["confidence"] == "HEURISTIC"
    assert verdict["reason"].strip(), "an unexplained bias is a gate failure"
    assert payload["findings"] == []
    assert payload["must_not_contain_findings"]


def test_lin_dataframe_tracks_each_column_as_its_own_node() -> None:
    """Per-column tracking is the whole point of the case."""
    payload = expectation("lin_dataframe")
    features = payload["feature_nodes_exact"]
    assert features == [
        "@feature:ask",
        "@feature:bid",
        "@feature:mid",
        "@feature:rank",
        "@feature:spread",
        "@feature:symbol",
    ]
    column_writes = [e for e in payload["lineage"] if e["kind"] == "COLUMN_WRITE"]
    assert len(column_writes) == 5
    pairs = {(e["source_id"], e["target_id"]) for e in column_writes}
    assert ("@feature:ask", "@feature:spread") in pairs
    assert ("@feature:bid", "@feature:spread") in pairs
    assert payload["must_not_contain_lineage"]


def test_res_getattr_computed_is_unknown_with_candidates_and_no_edge() -> None:
    """The case exists to prove the tool says 'I don't know' in the right
    place. A promoted guess here would pass a weaker test."""
    payload = expectation("res_getattr_computed")
    unresolved = payload["unresolved"]
    assert len(unresolved) == 1
    record = unresolved[0]
    assert record["reason"] == "DYNAMIC_NAME"
    assert record["candidate_confidence"] == "UNKNOWN"
    assert record["candidate_ids"] == [
        "res_getattr_computed::Helper.method_a",
        "res_getattr_computed::Helper.method_b",
    ]
    call_targets = {e["target_id"] for e in payload["edges"]}
    assert call_targets.isdisjoint(set(record["candidate_ids"]))
    assert len(payload["must_not_contain_edges"]) == 2


def test_lin_barrier_requires_a_barrier_at_the_eval_call() -> None:
    payload = expectation("lin_barrier")
    barriers = payload["barriers"]
    assert len(barriers) == 1
    barrier = barriers[0]
    assert barrier["reason"] == "DYNAMIC_NAME"
    assert barrier["element_id"] == "lin_barrier::evaluate"
    source_line = _source_line(barrier["span"])
    assert "eval(" in source_line, f"the barrier must sit at the eval call, not {source_line!r}"
    assert payload["must_not_contain_lineage"]


def _source_line(span: dict[str, Any]) -> str:
    text = (TESTS_DIR.parent / span["path"]).read_text().splitlines()
    return text[span["line"] - 1]


def test_every_event_id_is_the_padded_form_and_sorts_by_execution_order() -> None:
    """`TraceEvent.event_id` is `evt_` plus a zero-padded 8-digit ordinal.

    events.jsonl sorts by this field, so an unpadded counter puts evt_10
    before evt_2 and scrambles the one artifact whose natural reading order is
    execution order. Card 12 mints the padded form; round 3 of this corpus
    shipped `evt_1..evt_6` and is corrected here.
    """
    events_by_case = {
        directory.name: json.loads((directory / "expected.json").read_text()).get("events", [])
        for directory in all_case_dirs()
    }
    populated = {case: events for case, events in events_by_case.items() if events}
    assert populated, "no case declares an event stream"

    for case, events in sorted(populated.items()):
        for record in events:
            expected_id = f"evt_{record['sequence']:08d}"
            assert record["event_id"] == expected_id, (
                f"{case}: event_id {record['event_id']!r} must be {expected_id!r}"
            )
            caller = record.get("caller_event_id", "")
            if caller:
                assert _EVENT_ID.fullmatch(caller), f"{case}: caller {caller!r} is unpadded"
        ids = [record["event_id"] for record in events]
        sequences = [record["sequence"] for record in events]
        assert ids == sorted(ids), f"{case}: sorting by event_id must give execution order"
        assert sequences == sorted(sequences)


# ---------------------------------------------------------------------------
# The sentinel
# ---------------------------------------------------------------------------


def test_the_sentinel_marker_is_absent() -> None:
    """The empirical proof of constraint 1, asserted here as well as in every
    static card's own suite."""
    assert not SENTINEL_MARKER.exists(), (
        "the sentinel marker exists: something imported or executed "
        "tests/fixtures/sentinel instead of reading it as text"
    )


def test_the_sentinel_is_armed_at_module_top_level() -> None:
    """Proved by reading the AST, never by importing it.

    The write must sit at module top level -- not inside a function, not
    behind an `if`, not under a `__main__` guard -- so there is no way to
    import this module 'a little bit' without tripping it.
    """
    tree = ast.parse((SENTINEL / "__init__.py").read_text())
    top_level_writes = [
        node
        for node in tree.body
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "open"
            for item in node.items
        )
    ]
    assert len(top_level_writes) == 1, "exactly one unguarded top-level write"
    body_calls = [
        n for n in ast.walk(top_level_writes[0])
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "write"
    ]
    assert body_calls, "the top-level `with open(...)` must actually write"

    payload = json.loads((SENTINEL / "expected.json").read_text())
    assert payload["marker_path"] == str(SENTINEL_MARKER)
    assert payload["marker_must_be_absent_after_any_static_run"] is True


# ---------------------------------------------------------------------------
# Safety: no fixture reaches outside tests/fixtures/
# ---------------------------------------------------------------------------

#: Cases whose entire purpose is attempting to reach outside the sandbox. Each
#: one's expectation asserts the attempt was blocked and the marker absent.
_MAY_NAME_PATHS_OUTSIDE = frozenset(
    {
        "adv_dns",
        "adv_network",
        "adv_refuse_start",
        "adv_stale_graph",
        "adv_subprocess",
        "adv_undeclared_client",
        "adv_write_escape",
        "nar_absence",
        "sentinel",
    }
)


def test_no_ordinary_fixture_names_a_path_outside_the_corpus() -> None:
    """Mode B fixtures are never executed, and the Mode A ones that are must
    stay inside the sandbox. The adversarial cases are exempt by design: they
    exist to attempt the escape, and their expectations require it to fail."""
    sources = sorted(FIXTURES.rglob("*.py"))
    assert len(sources) >= 96
    offenders: list[str] = []
    for path in sources:
        if path.parent == FIXTURES:
            continue  # the corpus's own tooling, not a fixture
        case = _owning_case(path)
        if case in _MAY_NAME_PATHS_OUTSIDE:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue  # inv_non_utf8, on purpose
        for literal in _string_literals(text):
            if literal.startswith(("/", "~")) or "../" in literal:
                offenders.append(f"{path.relative_to(FIXTURES)}: {literal!r}")
    assert offenders == [], "fixtures naming paths outside the corpus:\n" + "\n".join(offenders)


def _owning_case(path: Path) -> str:
    relative = path.relative_to(FIXTURES).parts
    if relative[0] == "sentinel":
        return "sentinel"
    return relative[1] if len(relative) > 1 else relative[0]


def _string_literals(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []  # inv_syntax_error, on purpose
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


@pytest.mark.parametrize("case_id", ADVERSARIAL_CASES)
def test_each_adversarial_case_attempts_something_real(case_id: str) -> None:
    """Round 2 left these as docstring-only placeholders. A case that proves a
    control holds has to try to break it."""
    source = (MODE_A / case_id / "__init__.py").read_text()
    tree = ast.parse(source)
    functions = {
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "main" in functions, f"{case_id}: the harness invokes `main`"
    statements = [n for n in tree.body if not _is_docstring(n)]
    assert len(statements) >= 2, f"{case_id}: the program is a bare docstring"

    payload = expectation(case_id)
    assert payload["scenario"] == {"module": case_id, "function": "main"}
    required = [
        key
        for key in (
            "blocked_attempts_required",
            "required_run_record",
            "allowed_outcomes_per_route",
        )
        if payload.get(key)
    ]
    assert required, f"{case_id}: nothing is required of the harness"
    assert payload.get("marker_must_be_absent_after_the_run") or payload.get(
        "markers_that_must_not_exist_after_the_run"
    ), f"{case_id}: no marker makes the escape observable"


ATTEMPT_MARKERS: dict[str, tuple[str, ...]] = {
    "adv_network": ("socket.socket", "socket.create_connection", "http.client"),
    "adv_dns": ("gethostbyname", "getaddrinfo", "gethostbyaddr"),
    "adv_write_escape": ("os.symlink", "..", "open("),
    "adv_subprocess": ("subprocess.run", "subprocess.Popen", "os.system", "os.fork", "os.popen"),
    "adv_undeclared_client": ("submit_order", "os.environ"),
}


@pytest.mark.parametrize("case_id", sorted(ATTEMPT_MARKERS))
def test_adversarial_cases_use_every_route_they_claim(case_id: str) -> None:
    """Each control has several doors. A case that tries one proves one."""
    source = (MODE_A / case_id / "__init__.py").read_text()
    missing = [marker for marker in ATTEMPT_MARKERS[case_id] if marker not in source]
    assert missing == [], f"{case_id}: does not attempt {missing}"


@pytest.mark.parametrize("case_id", ADVERSARIAL_CASES)
def test_adversarial_attempts_are_not_made_at_import_time(case_id: str) -> None:
    """The attempt belongs inside `main`, so importing the module by accident
    does nothing. Only the harness, with its controls active, triggers it."""
    tree = ast.parse((MODE_A / case_id / "__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
            continue
        if _is_docstring(node):
            continue
        pytest.fail(f"{case_id}: top-level {type(node).__name__} runs on import")


# ---------------------------------------------------------------------------
# The programs themselves
# ---------------------------------------------------------------------------

#: The two cases whose source is deliberately unreadable.
_DELIBERATELY_BROKEN = {
    "inv_syntax_error": "SyntaxError",
    "inv_non_utf8": "UnicodeDecodeError",
}


def test_every_fixture_program_parses_except_the_two_that_must_not() -> None:
    sources = [p for p in sorted(FIXTURES.rglob("*.py")) if p.parent != FIXTURES]
    assert len(sources) >= 96
    for path in sources:
        case = _owning_case(path)
        expected_failure = _DELIBERATELY_BROKEN.get(case)
        try:
            ast.parse(path.read_bytes())
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            assert expected_failure, f"{path.relative_to(FIXTURES)} does not parse: {exc}"
            continue
        assert not expected_failure, f"{case} must fail to parse with {expected_failure}"


def test_inv_blob_byte_size_is_measured_not_counted() -> None:
    """Round 2 gave 1048576 against a true 1048578: the triple-quoted literal
    carries a leading and a trailing newline inside the quotes."""
    payload = expectation("inv_blob")
    blob = next(e for e in payload["elements"] if e["kind"] == "BLOB")
    source = (MODE_B / "inv_blob" / "__init__.py").read_text()
    literal = next(
        node.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
    assert blob["byte_size"] == len(literal.encode("utf-8"))
    assert blob["byte_size"] == 1048578
