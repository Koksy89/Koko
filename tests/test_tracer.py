"""Card 12 -- runtime tracer and value capture.

Two kinds of test live here, deliberately.

* Tests that **execute a fixture**. Only `tests/fixtures/mode_a/` programs are
  ever executed, only by this file and card 11's, and only in-process inside a
  collector -- the tracer starts no process of its own.
* Tests that **construct a recording**. Materialisation is a pure function of a
  recording plus the static graph, so branch resolution, exception disposition,
  contradictions, nondeterminism and thread ordering can all be graded on
  inputs written by hand. That is also the only way to grade them before card 8
  lands the remaining `run_*` fixtures.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re
import posixpath
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

import pytest

from cascade_map.contracts.interfaces import (
    BlockKind,
    CaptureStatus,
    CFGBlock,
    CFGEdge,
    Confidence,
    DecisionPoint,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    EventKind,
    LineageEdge,
    LineageKind,
    Method,
    OrderKind,
    OrderNode,
    Provenance,
    RunObserver,
    RunRecord,
    SourceSpan,
    canonical_dumps,
    canonical_jsonl,
)
from cascade_map.tracer import (
    ADDRESS_NOTE,
    CaptureLimits,
    CodeLocation,
    ContradictionKind,
    NondeterminismKind,
    ObsKind,
    RawObservation,
    Recording,
    RedactionPolicy,
    StaticIndex,
    TraceCollector,
    TraceRefused,
    Tracer,
    capture_value,
    capture_values,
    refusal_reason,
    stable_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODE_A = REPO_ROOT / "tests" / "fixtures" / "mode_a"
TRACER_PKG = REPO_ROOT / "src" / "cascade_map" / "tracer"
THIS_FILE = Path(__file__).name
EXPECTED_ARTIFACTS = {
    "events.jsonl",
    "contradictions.jsonl",
    "nondeterminism.jsonl",
    "mapping.json",
}

AST = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_run(
    run_id: str = "run_001",
    controls: dict[str, bool] | None = None,
    refused: bool = False,
    refusal_reason_text: str = "",
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        target_hashes={"__init__.py": "deadbeef"},
        graph_hash="graph-hash",
        scenario="fixture",
        interpreter="cpython",
        controls_active=(
            controls
            if controls is not None
            else {
                "network_blocked": True,
                "filesystem_sandboxed": True,
                "subprocess_blocked": True,
            }
        ),
        blocked=(),
        refused=refused,
        refusal_reason=refusal_reason_text,
    )


def element(
    element_id: str,
    qualname: str,
    path: str,
    line: int,
    end_line: int | None = None,
    kind: ElementKind = ElementKind.FUNCTION,
    module: str = "m",
) -> Element:
    return Element(
        id=element_id,
        kind=kind,
        name=qualname.split(".")[-1] or module,
        qualname=qualname,
        module=module,
        span=SourceSpan(path=path, line=line, end_line=end_line),
        provenance=AST,
        content_hash="hash",
    )


class RecordingBuilder:
    """Hand-written recordings: the evidence a real run would have left."""

    def __init__(self) -> None:
        self.observations: list[RawObservation] = []
        self._seq: dict[int, int] = {}
        self._arrival = 0

    def add(
        self,
        kind: ObsKind,
        *,
        frame_key: int = 1,
        parent: int = 0,
        depth: int = 0,
        path: str = "m.py",
        line: int = 1,
        first_line: int = 1,
        qualname: str = "decide",
        slot: int = 0,
        values: dict[str, Any] | None = None,
        detail: dict[str, str] | None = None,
        under_root: bool = True,
        synthetic: bool = False,
        arrival: int | None = None,
    ) -> RawObservation:
        self._arrival += 1
        self._seq[slot] = self._seq.get(slot, 0) + 1
        observation = RawObservation(
            kind=kind,
            thread_slot=slot,
            thread_seq=self._seq[slot],
            arrival=self._arrival if arrival is None else arrival,
            frame_key=frame_key,
            parent_frame_key=parent,
            depth=depth,
            path=path,
            line=line,
            first_line=first_line,
            qualname=qualname,
            under_root=under_root,
            synthetic=synthetic,
            values=capture_values(values or {}),
            detail=detail or {},
        )
        self.observations.append(observation)
        return observation

    def build(self, **header: Any) -> Recording:
        base: dict[str, Any] = {
            "recording_version": "1",
            "run_id": "run_001",
            "observation_count": len(self.observations),
            "dropped_observations": 0,
            "max_observations": 1000,
            "hash_randomization": False,
            "thread_count": len({o.thread_slot for o in self.observations}) or 1,
            "external_frames": {},
        }
        base.update(header)
        return Recording(header=base, observations=tuple(self.observations))


def decision_index(is_sink: bool = True) -> StaticIndex:
    """A three-block branch in one element, with a decision point on it."""
    return StaticIndex(
        root="/nowhere",
        elements=[element("m::decide", "decide", "m.py", 10, 20)],
        cfg_blocks=[
            CFGBlock("b1", "m::decide", BlockKind.BRANCH, SourceSpan("m.py", 12), AST),
            CFGBlock("b2", "m::decide", BlockKind.NORMAL, SourceSpan("m.py", 13, 14), AST),
            CFGBlock("b3", "m::decide", BlockKind.NORMAL, SourceSpan("m.py", 16, 17), AST),
        ],
        cfg_edges=[
            CFGEdge("e1", "b1", "b2", condition="score > 5", taken_when=True),
            CFGEdge("e2", "b1", "b3", condition="score > 5", taken_when=False),
        ],
        decisions=[
            DecisionPoint(
                id="d1",
                element_id="m::decide",
                condition_source="score > 5",
                reads_ids=("@feature:score",),
                outcomes=(("approve", "b2"), ("reject", "b3")),
                is_sink=is_sink,
            )
        ],
    )


def branch_recording(next_line: int) -> Recording:
    builder = RecordingBuilder()
    builder.add(ObsKind.CALL, line=10, first_line=10)
    builder.add(
        ObsKind.BRANCH_COND,
        line=12,
        first_line=10,
        values={"score": 7},
        detail={
            "block_id": "b1",
            "decision_id": "d1",
            "condition": "score > 5",
            "is_sink": "1",
        },
    )
    builder.add(
        ObsKind.BRANCH_NEXT,
        line=next_line,
        first_line=10,
        detail={"block_id": "b1", "cond_line": "12", "via": "line"},
    )
    builder.add(ObsKind.RETURN, line=next_line, first_line=10, values={"return_value": "ok"})
    return builder.build()


# -- fixture loading --------------------------------------------------------


def case_dirs() -> list[Path]:
    if not MODE_A.is_dir():
        return []
    return sorted(
        path
        for path in MODE_A.iterdir()
        if path.is_dir() and path.name.startswith("run_") and (path / "expected.json").is_file()
    )


def load_case(case: Path) -> tuple[dict[str, Any], StaticIndex]:
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    case_rel = case.relative_to(REPO_ROOT).as_posix()
    elements = []
    for raw in expected.get("elements", []):
        span = raw["span"]
        path = posixpath.relpath(span["path"], case_rel)
        elements.append(
            Element(
                id=raw["id"],
                kind=ElementKind(raw["kind"]),
                name=raw["name"],
                qualname=raw["qualname"],
                module=raw["module"],
                span=SourceSpan(
                    path=path, line=span["line"], end_line=span.get("end_line")
                ),
                provenance=AST,
                content_hash=raw.get("content_hash", ""),
            )
        )
    return expected, StaticIndex(root=str(case), elements=elements)


def import_case(case: Path) -> Any:
    sys.dont_write_bytecode = True  # never write into tests/fixtures/
    parent = str(case.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(case.name)


def run_case(case: Path, index: StaticIndex, **kwargs: Any) -> tuple[Tracer, RunRecord, Any]:
    """Execute one Mode A fixture inside a collector, in this process."""
    module = import_case(case)
    entry = getattr(module, "main", None)
    if entry is None:
        pytest.skip(f"fixture {case.name} has no main()")
    run = make_run()
    tracer = Tracer(index, **kwargs)
    with tracer.collector(run) as collector:
        try:
            entry()
        except Exception:  # a fixture may raise on purpose; the trace still counts
            pass
    recording = collector.recording()
    tracer.hold(run, recording)
    return tracer, run, recording


def linear_case() -> Path:
    case = MODE_A / "run_linear"
    if not case.is_dir():
        pytest.skip("fixture run_linear not present (card 8)")
    return case


# ---------------------------------------------------------------------------
# bounded capture
# ---------------------------------------------------------------------------


class Tattletale:
    """A value that reports being rendered. Redaction must never render."""

    def __repr__(self) -> str:
        raise AssertionError("a redacted value was rendered")


class Exploding:
    def __repr__(self) -> str:
        raise RuntimeError("no repr for you")


class FakeFrame:
    """A dataframe-shaped value, without importing pandas."""

    def __init__(self, rows: int) -> None:
        self._rows = rows
        self.columns = ["price", "volume", "flag"]
        self.dtypes = {"price": "float64", "volume": "int64", "flag": "object"}
        self.shape = (rows, 3)
        self.nbytes = rows * 24

    def isna(self) -> "FakeFrame":
        return self

    def sum(self) -> dict[str, int]:
        return {"price": 3, "volume": 0, "flag": 7}

    def head(self, count: int) -> "FakeFrameHead":
        return FakeFrameHead(min(count, self._rows))


class FakeFrameHead:
    def __init__(self, count: int) -> None:
        self._count = count

    def to_dict(self, orient: str) -> list[dict[str, int]]:
        return [{"price": i, "volume": i * 2, "flag": "x"} for i in range(self._count)]


def test_scalar_is_captured_whole() -> None:
    capture = capture_value("x", 7)
    assert capture.status is CaptureStatus.FULL
    assert capture.repr_text == "7"
    assert capture.type_name == "int"


def test_long_string_is_summarized_never_silently_truncated() -> None:
    capture = capture_value("blob", "x" * 100_000)
    assert capture.status is CaptureStatus.SUMMARIZED
    assert capture.repr_text.startswith("<SUMMARIZED:")
    assert capture.original_size == 100_000
    assert "characters" in capture.reason


def test_large_frame_is_summarized_with_shape_dtypes_nulls_and_sample() -> None:
    capture = capture_value("frame", FakeFrame(250_000))
    assert capture.status is CaptureStatus.SUMMARIZED
    assert capture.shape == "(250000, 3)"
    assert "rows=250000" in capture.repr_text
    assert "price:float64" in capture.repr_text
    assert "flag:7" in capture.repr_text  # null counts
    assert "head=" in capture.repr_text
    assert capture.original_size == 250_000 * 24
    assert "original_size is bytes" in capture.reason


def test_every_bounded_capture_announces_itself() -> None:
    values = {
        "text": "y" * 5000,
        "frame": FakeFrame(10),
        "rows": list(range(5000)),
        "blob": b"\x00" * 9000,
        "broken": Exploding(),
        "password": "hunter2",
    }
    assert len(values) == 6
    for name, value in values.items():
        capture = capture_value(name, value)
        assert capture.status is not CaptureStatus.FULL, name
        assert capture.repr_text.startswith(f"<{capture.status}"), name
        assert capture.reason, name
        if capture.status is CaptureStatus.SUMMARIZED:
            assert capture.original_size > 0, name


def test_redaction_happens_at_capture_time_without_rendering() -> None:
    capture = capture_value("api_key", Tattletale())
    assert capture.status is CaptureStatus.REDACTED
    assert capture.repr_text == "<REDACTED>"
    assert "sensitive pattern" in capture.reason


def test_owner_declared_names_and_elements_are_redacted() -> None:
    policy = RedactionPolicy.from_owner(names=["account"], element_ids=["m::secret"])
    assert policy.reason_for("account").startswith("value name 'account' is on the owner")
    assert "m::secret" in policy.reason_for("anything", "m::secret")
    assert policy.reason_for("harmless") == ""


def test_capture_never_raises() -> None:
    capture = capture_value("broken", Exploding())
    assert capture.status is CaptureStatus.DROPPED
    assert "RuntimeError" in capture.reason


def test_per_event_budget_drops_explicitly_and_deterministically() -> None:
    limits = CaptureLimits(max_event_chars=600)
    values = {f"v{index}": "z" * 900 for index in range(6)}
    first = capture_values(values, limits=limits)
    second = capture_values(values, limits=limits)
    assert canonical_dumps(first) == canonical_dumps(second)
    dropped = [name for name, cap in first.items() if cap.status is CaptureStatus.DROPPED]
    assert dropped, "the budget must bite"
    for name in dropped:
        assert "budget" in first[name].reason
        assert first[name].original_size > 0
    assert len(canonical_dumps(first)) < 6 * 900


def test_captured_floats_never_reach_the_artifact_as_floats() -> None:
    capture = capture_value("ratio", 0.125)
    assert capture.status is CaptureStatus.FULL
    assert capture.repr_text == "0.125"
    canonical_dumps(capture)  # would raise on a float payload


# ---------------------------------------------------------------------------
# refusal: no evidence from a run whose isolation was never established
# ---------------------------------------------------------------------------


def test_collector_refuses_a_refused_run() -> None:
    run = make_run(refused=True, refusal_reason_text="network control unavailable")
    with pytest.raises(TraceRefused) as raised:
        TraceCollector(run, StaticIndex(root="/nowhere"))
    assert "network control unavailable" in raised.value.reason
    assert "nothing to trace" in raised.value.reason


@pytest.mark.parametrize(
    "controls,missing",
    [
        ({"filesystem_sandboxed": True, "subprocess_blocked": True}, "network"),
        ({"network_blocked": True, "subprocess_blocked": True}, "filesystem"),
        ({"network_blocked": True, "filesystem_sandboxed": True}, "subprocess"),
        ({}, "network"),
    ],
)
def test_collector_refuses_when_a_control_is_not_reported(
    controls: dict[str, bool], missing: str
) -> None:
    run = make_run(controls=controls)
    with pytest.raises(TraceRefused) as raised:
        TraceCollector(run, StaticIndex(root="/nowhere"))
    assert missing in raised.value.reason


def test_collector_refuses_when_a_control_is_inactive() -> None:
    run = make_run(
        controls={
            "network_blocked": False,
            "filesystem_sandboxed": True,
            "subprocess_blocked": True,
        }
    )
    with pytest.raises(TraceRefused) as raised:
        TraceCollector(run, StaticIndex(root="/nowhere"))
    assert "'network'" in raised.value.reason
    assert "reach the real world" in raised.value.reason


def test_refusal_reads_control_names_the_harness_may_choose() -> None:
    run = make_run(
        controls={
            "outbound_network_blocked": True,
            "filesystem_writes_redirected": True,
            "process_spawning_denied": True,
            "clock_frozen": False,
        }
    )
    assert refusal_reason(run) == ""


def test_tracing_a_refused_run_is_refused_even_with_a_recording() -> None:
    index = StaticIndex(root="/nowhere")
    tracer = Tracer(index)
    good = make_run()
    tracer.hold(good, RecordingBuilder().build())
    refused = make_run(refused=True, refusal_reason_text="sandbox unavailable")
    with pytest.raises(TraceRefused):
        tracer.trace(refused)


def test_tracer_refuses_instead_of_inventing_a_run(tmp_path: Path) -> None:
    tracer = Tracer(StaticIndex(root="/nowhere"), recordings_dir=tmp_path)
    with pytest.raises(TraceRefused) as raised:
        tracer.trace(make_run("run_missing"))
    assert "must be collected inside the card 11 harness" in raised.value.reason


def test_the_tracer_cannot_start_a_process() -> None:
    """The card says the tracer never starts a process. This proves it.

    Checked on the parse tree, not on the text: the word "subprocess" appears
    all over this package as the *name of a harness control the tracer demands*,
    and a text search would either miss the real thing or flag the prose.
    """
    forbidden_imports = {
        "subprocess",
        "multiprocessing",
        "pty",
        "ctypes",
        "socket",
        "asyncio",
        "concurrent",
    }
    forbidden_attributes = {
        "system",
        "fork",
        "forkpty",
        "popen",
        "execv",
        "execve",
        "execvp",
        "spawnv",
        "spawnl",
        "posix_spawn",
        "startfile",
    }
    forbidden_builtins = {"eval", "exec", "compile", "__import__"}
    sources = sorted(TRACER_PKG.glob("*.py"))
    assert len(sources) >= 9, f"the tracer package has no sources to check: {sources}"
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_imports, f"{source.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in forbidden_imports, f"{source.name} imports from {root}"
            elif isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Attribute):
                    assert (
                        target.attr not in forbidden_attributes
                    ), f"{source.name} calls {target.attr}"
                elif isinstance(target, ast.Name):
                    assert (
                        target.id not in forbidden_builtins
                    ), f"{source.name} calls the builtin {target.id}"


# ---------------------------------------------------------------------------
# executing the Mode A fixtures
# ---------------------------------------------------------------------------


def assert_expected_events(expected: dict[str, Any], actual: Iterable[Any]) -> None:
    """Grade against a hand-written expectation.

    Event IDs are compared as a *relation*, not as strings: `expected.json`
    writes `evt_1` while the tracer mints zero-padded IDs so that events.jsonl,
    which schema.json sorts by event_id, reads in execution order. Every other
    declared field is compared exactly.
    """
    by_sequence = {event.sequence: event for event in actual}
    assert expected.get("events"), "nothing to grade: the expectation declares no events"
    assert by_sequence, "nothing was traced"
    id_map: dict[str, str] = {}
    for want in expected.get("events", []):
        got = by_sequence.get(want["sequence"])
        assert got is not None, f"no event at sequence {want['sequence']}"
        if "event_id" in want:
            id_map[want["event_id"]] = got.event_id
        for field in ("run_id", "element_id", "depth", "branch_taken"):
            if field in want:
                assert getattr(got, field) == want[field], (field, want["sequence"])
        if "kind" in want:
            assert got.kind == EventKind(want["kind"]), want["sequence"]
    for want in expected.get("events", []):
        got = by_sequence[want["sequence"]]
        if "caller_event_id" in want:
            assert got.caller_event_id == id_map.get(want["caller_event_id"], "")
        for name, capture in want.get("values", {}).items():
            assert name in got.values, (want["sequence"], name)
            actual_capture = got.values[name]
            assert actual_capture.status == CaptureStatus(capture["status"])
            if "repr_text" in capture:
                assert actual_capture.repr_text == capture["repr_text"]
            if "type_name" in capture:
                assert actual_capture.type_name == capture["type_name"]


@pytest.mark.parametrize("case", case_dirs(), ids=lambda path: path.name)
def test_mode_a_fixture_matches_its_expectation(case: Path) -> None:
    expected, index = load_case(case)
    if not expected.get("events"):
        pytest.skip(
            f"{case.name} is a placeholder: it declares no expected events, so there is "
            "nothing to grade against (card 8 gap)"
        )
    tracer, run, _ = run_case(case, index)
    result = tracer.result(run)
    assert_expected_events(expected, result.events)


def test_run_linear_maps_every_event_to_a_static_id() -> None:
    case = linear_case()
    _, index = load_case(case)
    tracer, run, _ = run_case(case, index)
    result = tracer.result(run)
    assert result.mapping.total_events == 6
    assert result.mapping.unmapped_events == 0
    assert result.mapping.rate_permille == 1000
    assert result.mapping.rate_text == "6/6 target events mapped (100.0%)"
    assert result.mapping.elements_entered == 3
    assert [event.element_id for event in result.events] == [
        "run_linear::main",
        "run_linear::step_one",
        "run_linear::step_one",
        "run_linear::step_two",
        "run_linear::step_two",
        "run_linear::main",
    ]


def test_arguments_and_returns_are_captured_by_construction() -> None:
    case = linear_case()
    _, index = load_case(case)
    tracer, run, _ = run_case(case, index)
    events = tracer.result(run).events
    call_two = next(
        event
        for event in events
        if event.kind is EventKind.CALL and event.element_id == "run_linear::step_two"
    )
    assert call_two.values["x"].repr_text == "1"
    assert call_two.values["x"].status is CaptureStatus.FULL
    returns = [event for event in events if event.kind is EventKind.RETURN]
    assert [event.values["return_value"].repr_text for event in returns] == ["1", "3", "3"]


def test_every_event_carries_runtime_provenance_with_run_and_event_id() -> None:
    case = linear_case()
    _, index = load_case(case)
    tracer, run, _ = run_case(case, index)
    events = tracer.result(run).events
    assert len(events) == 6, "a loop over an empty trace would assert nothing"
    for event in events:
        assert event.provenance is not None
        assert event.provenance.method is Method.RUNTIME_OBSERVED
        assert event.provenance.run_id == run.run_id
        assert event.provenance.event_ids == (event.event_id,)
        assert event.provenance.span is not None
        assert not event.provenance.span.path.startswith("/")


def test_a_sink_return_produces_a_decision_event() -> None:
    case = linear_case()
    expected, _ = load_case(case)
    _, index = load_case(case)
    index = StaticIndex(
        root=str(case),
        elements=list(index.elements),
        sink_element_ids=["run_linear::main"],
    )
    tracer, run, _ = run_case(case, index)
    decisions = [
        event for event in tracer.result(run).events if event.kind is EventKind.DECISION
    ]
    assert len(decisions) == 1
    assert decisions[0].element_id == "run_linear::main"
    assert decisions[0].values["decision"].repr_text == "3"


def test_replaying_a_recorded_run_is_byte_identical(tmp_path: Path) -> None:
    case = linear_case()
    _, index = load_case(case)
    tracer, run, recording = run_case(case, index)
    path = tmp_path / run.run_id / "recording.jsonl"
    recording.write(path)

    first = Tracer(index, recordings_dir=tmp_path)
    second = Tracer(index, recordings_dir=tmp_path)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    files_a = first.emit(first.result(run), out_a)
    files_b = second.emit(second.result(run), out_b)
    assert files_a == files_b
    assert set(files_a) == EXPECTED_ARTIFACTS
    for name in files_a:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()
    assert Recording.read(path).dumps() == recording.dumps()


def test_two_runs_of_a_deterministic_fixture_agree() -> None:
    case = linear_case()
    _, index = load_case(case)
    first_tracer, first_run, _ = run_case(case, index)
    second_tracer, second_run, _ = run_case(case, index)
    assert first_tracer.events_jsonl(
        first_tracer.result(first_run).events
    ) == second_tracer.events_jsonl(second_tracer.result(second_run).events)


def test_events_file_is_sorted_by_event_id_in_execution_order() -> None:
    case = linear_case()
    _, index = load_case(case)
    tracer, run, _ = run_case(case, index)
    result = tracer.result(run)
    lines = tracer.events_jsonl(result.events).splitlines()
    assert [json.loads(line)["sequence"] for line in lines] == [1, 2, 3, 4, 5, 6]
    assert lines == sorted(lines, key=lambda line: json.loads(line)["event_id"])


def test_truncation_of_the_trace_itself_is_announced() -> None:
    case = linear_case()
    _, index = load_case(case)
    tracer, run, recording = run_case(case, index, max_observations=2)
    assert recording.header["dropped_observations"] > 0
    result = tracer.result(run)
    truncation = [
        event
        for event in result.events
        if event.provenance is not None and "tracing stopped" in event.provenance.note
    ]
    assert len(truncation) == 1
    assert truncation[0].kind is EventKind.UNMAPPED
    assert result.mapping.dropped_observations > 0
    kinds = {observation.kind for observation in result.nondeterminism}
    assert NondeterminismKind.TRACE_TRUNCATED in kinds


# ---------------------------------------------------------------------------
# mapping: what the static analysis missed
# ---------------------------------------------------------------------------


def test_dynamically_created_code_is_unmapped_not_dropped() -> None:
    builder = RecordingBuilder()
    builder.add(
        ObsKind.CALL,
        path="<string>",
        line=1,
        first_line=1,
        qualname="generated",
        under_root=False,
        synthetic=True,
    )
    builder.add(
        ObsKind.RETURN,
        path="<string>",
        line=1,
        first_line=1,
        qualname="generated",
        under_root=False,
        synthetic=True,
        values={"return_value": 1},
    )
    tracer = Tracer(StaticIndex(root="/nowhere"))
    result = tracer.materialise(make_run(), builder.build())
    assert [event.kind for event in result.events] == [EventKind.UNMAPPED] * 2
    for event in result.events:
        assert event.element_id == ""
        assert event.provenance is not None
        assert event.provenance.span is not None
        assert event.provenance.span.path == "<string>"
        assert "dynamically created code object" in event.provenance.note
    assert result.mapping.unmapped_reasons == {"DYNAMIC_CODE": 2}
    assert result.mapping.rate_permille == 0
    assert result.mapping.rate_text == "0/2 target events mapped (0.0%)"


def test_code_the_inventory_never_saw_is_unmapped_with_location_and_reason() -> None:
    builder = RecordingBuilder()
    builder.add(ObsKind.CALL, path="m.py", line=40, first_line=40, qualname="ghost")
    index = StaticIndex(root="/nowhere", elements=[element("m::decide", "decide", "m.py", 10, 20)])
    result = Tracer(index).materialise(make_run(), builder.build())
    event = result.events[0]
    assert event.kind is EventKind.UNMAPPED
    assert event.provenance is not None
    assert "no static element at m.py" in event.provenance.note
    assert "'ghost'" in event.provenance.note
    assert result.mapping.unmapped_reasons == {"MISSING_FROM_INVENTORY": 1}


def test_an_ambiguous_mapping_claims_nothing() -> None:
    index = StaticIndex(
        root="/nowhere",
        elements=[
            element("m::f", "f", "m.py", 1, 30),
            element("m::f#2", "f", "m.py", 1, 30),
        ],
    )
    mapping = index.map_code(
        CodeLocation(path="m.py", qualname="f", line=5, first_line=5, under_root=True)
    )
    assert mapping.element_id == ""
    assert mapping.confidence is Confidence.UNKNOWN
    assert "ambiguous" in mapping.reason
    assert "m::f,m::f#2" in mapping.reason


def test_nested_function_qualnames_map_through_locals() -> None:
    index = StaticIndex(
        root="/nowhere", elements=[element("m::outer.inner", "outer.inner", "m.py", 4, 9)]
    )
    mapping = index.map_code(
        CodeLocation(
            path="m.py", qualname="outer.<locals>.inner", line=5, first_line=4, under_root=True
        )
    )
    assert mapping.element_id == "m::outer.inner"
    assert mapping.confidence is Confidence.RESOLVED


def test_external_code_is_reported_not_claimed() -> None:
    index = StaticIndex(root="/nowhere")
    location = index.relocate("/usr/lib/python3.11/random.py")
    assert location.path == "<stdlib>/random.py"
    assert index.map_code(location).reason.endswith("no static element exists for it")


# ---------------------------------------------------------------------------
# branches, decisions, features, exceptions
# ---------------------------------------------------------------------------


def test_the_branch_actually_taken_is_recorded_with_the_values_read() -> None:
    result = Tracer(decision_index()).materialise(make_run(), branch_recording(13))
    branch = next(event for event in result.events if event.kind is EventKind.BRANCH)
    assert branch.branch_taken == "approve"
    assert branch.element_id == "m::decide"
    assert branch.values["score"].repr_text == "7"
    assert branch.provenance is not None
    assert branch.provenance.confidence is Confidence.RESOLVED


def test_the_other_branch_is_recorded_when_it_is_the_one_taken() -> None:
    result = Tracer(decision_index()).materialise(make_run(), branch_recording(16))
    branch = next(event for event in result.events if event.kind is EventKind.BRANCH)
    assert branch.branch_taken == "reject"


def test_an_unrecognisable_branch_target_says_so_rather_than_guessing() -> None:
    result = Tracer(decision_index()).materialise(make_run(), branch_recording(99))
    branch = next(event for event in result.events if event.kind is EventKind.BRANCH)
    assert branch.branch_taken == "line:99"
    assert branch.provenance is not None
    assert branch.provenance.confidence is Confidence.UNKNOWN
    assert "cannot be named from the static graph" in branch.provenance.note


def test_a_sink_decision_emits_the_decision_it_produced() -> None:
    result = Tracer(decision_index()).materialise(make_run(), branch_recording(13))
    decisions = [event for event in result.events if event.kind is EventKind.DECISION]
    assert [event.branch_taken for event in decisions][0] == "approve"
    assert decisions[0].element_id == "m::decide"


def test_a_branch_that_never_resolved_is_still_emitted() -> None:
    builder = RecordingBuilder()
    builder.add(ObsKind.CALL, line=10, first_line=10)
    builder.add(
        ObsKind.BRANCH_COND,
        line=12,
        first_line=10,
        values={"score": 7},
        detail={"block_id": "b1", "decision_id": "d1", "condition": "score > 5"},
    )
    result = Tracer(decision_index()).materialise(make_run(), builder.build())
    branch = next(event for event in result.events if event.kind is EventKind.BRANCH)
    assert branch.branch_taken == ""
    assert branch.provenance is not None
    assert "never resolved" in branch.provenance.note


def test_a_tracked_feature_write_lands_on_the_feature_id() -> None:
    index = StaticIndex(
        root="/nowhere",
        elements=[element("m::decide", "decide", "m.py", 10, 20)],
        lineage=[
            LineageEdge(
                id="l1",
                kind=LineageKind.COLUMN_WRITE,
                source_id="m::decide",
                target_id="@feature:score",
                provenance=AST,
                span=SourceSpan("m.py", 13),
            )
        ],
    )
    assert index.feature_lines("m.py") == {13: ("@feature:score",)}
    assert index.traces_lines("m.py") is True
    builder = RecordingBuilder()
    builder.add(ObsKind.CALL, line=10, first_line=10)
    builder.add(
        ObsKind.FEATURE,
        line=13,
        first_line=10,
        values={"score": 7},
        detail={"feature_id": "@feature:score", "feature_name": "score"},
    )
    result = Tracer(index).materialise(make_run(), builder.build())
    write = next(event for event in result.events if event.kind is EventKind.FEATURE_WRITE)
    assert write.element_id == "@feature:score"
    assert write.values["score"].repr_text == "7"
    assert write.provenance is not None
    assert "written by m::decide" in write.provenance.note


def _exception_recording(handled: bool) -> Recording:
    builder = RecordingBuilder()
    builder.add(ObsKind.CALL, line=10, first_line=10)
    builder.add(
        ObsKind.EXCEPTION,
        line=13,
        first_line=10,
        values={"exception_type": "ValueError", "exception_message": "boom"},
        detail={"exception_type": "ValueError"},
    )
    if handled:
        builder.add(ObsKind.HANDLER, line=15, first_line=10)
        builder.add(
            ObsKind.RETURN,
            line=16,
            first_line=10,
            values={"return_value": None},
            detail={"exception_seen": "1", "handler_seen": "1", "unwinding": "0"},
        )
    else:
        builder.add(
            ObsKind.RETURN,
            line=13,
            first_line=10,
            detail={"exception_seen": "1", "handler_seen": "0", "unwinding": "1"},
        )
    return builder.build()


def test_a_swallowed_exception_is_recorded_as_caught() -> None:
    result = Tracer(decision_index()).materialise(make_run(), _exception_recording(True))
    raised = next(event for event in result.events if event.kind is EventKind.EXCEPTION)
    assert raised.values["exception_type"].repr_text == "'ValueError'"
    assert raised.provenance is not None
    assert "disposition=CAUGHT" in raised.provenance.note
    assert raised.provenance.confidence is Confidence.RESOLVED


def test_a_propagating_exception_is_recorded_as_propagated() -> None:
    result = Tracer(decision_index()).materialise(make_run(), _exception_recording(False))
    raised = next(event for event in result.events if event.kind is EventKind.EXCEPTION)
    assert raised.provenance is not None
    assert "disposition=PROPAGATED" in raised.provenance.note
    returned = next(event for event in result.events if event.kind is EventKind.RETURN)
    assert returned.provenance is not None
    assert "left via an exception" in returned.provenance.note


# ---------------------------------------------------------------------------
# contradictions: recorded, never merged into the graph
# ---------------------------------------------------------------------------


def linear_graph(extra_edges: Iterable[Edge] = (), extra_elements: Iterable[Element] = ()):
    case = linear_case()
    _, index = load_case(case)
    return case, StaticIndex(
        root=str(case),
        elements=list(index.elements) + list(extra_elements),
        edges=list(extra_edges),
    )


def call_edge(edge_id: str, source: str, target: str) -> Edge:
    return Edge(
        id=edge_id,
        kind=EdgeKind.CALLS,
        source_id=source,
        target_id=target,
        provenance=Provenance(method=Method.SCOPE_LOOKUP, confidence=Confidence.RESOLVED),
    )


def test_an_edge_the_run_never_took_becomes_a_contradiction() -> None:
    unused = element(
        "run_linear::step_three", "step_three", "__init__.py", 30, 32, module="run_linear"
    )
    case, index = linear_graph(
        extra_edges=[
            call_edge("edge_main_one", "run_linear::main", "run_linear::step_one"),
            call_edge("edge_main_two", "run_linear::main", "run_linear::step_two"),
            call_edge("edge_main_three", "run_linear::main", "run_linear::step_three"),
        ],
        extra_elements=[unused],
    )
    before = canonical_jsonl(index.elements) + canonical_jsonl(index.edges)
    tracer, run, _ = run_case(case, index)
    result = tracer.result(run)
    not_taken = [
        item
        for item in result.contradictions
        if item.kind is ContradictionKind.EDGE_NOT_TAKEN
    ]
    assert len(not_taken) == 1
    assert not_taken[0].static_ids == ("edge_main_three",)
    assert not_taken[0].observed_ids == ("run_linear::main", "run_linear::step_three")
    assert not_taken[0].provenance.method is Method.RUNTIME_OBSERVED
    assert not_taken[0].provenance.run_id == run.run_id
    assert not_taken[0].provenance.event_ids
    assert "not a verdict on the graph" in not_taken[0].summary
    assert canonical_jsonl(index.elements) + canonical_jsonl(index.edges) == before


def test_a_call_the_graph_did_not_predict_becomes_a_contradiction() -> None:
    case, index = linear_graph(
        extra_edges=[call_edge("edge_main_one", "run_linear::main", "run_linear::step_one")]
    )
    tracer, run, _ = run_case(case, index)
    unpredicted = [
        item
        for item in tracer.result(run).contradictions
        if item.kind is ContradictionKind.CALL_NOT_PREDICTED
    ]
    assert [item.observed_ids for item in unpredicted] == [
        ("run_linear::main", "run_linear::step_two")
    ]


def test_an_order_the_run_reversed_becomes_a_contradiction() -> None:
    case = linear_case()
    _, base = load_case(case)
    index = StaticIndex(
        root=str(case),
        elements=list(base.elements),
        edges=[
            call_edge("edge_main_one", "run_linear::main", "run_linear::step_one"),
            call_edge("edge_main_two", "run_linear::main", "run_linear::step_two"),
        ],
        order_nodes=[
            OrderNode(
                id="order_main",
                kind=OrderKind.SEQUENCE,
                element_ids=("run_linear::step_two", "run_linear::step_one"),
            )
        ],
    )
    tracer, run, _ = run_case(case, index)
    reversed_order = [
        item
        for item in tracer.result(run).contradictions
        if item.kind is ContradictionKind.ORDER_DIFFERS
    ]
    assert len(reversed_order) == 1
    assert reversed_order[0].static_ids == ("order_main",)
    assert "ran first in this run" in reversed_order[0].summary


# ---------------------------------------------------------------------------
# nondeterminism: recorded, not hidden
# ---------------------------------------------------------------------------


def test_clock_and_randomness_references_are_recorded_as_observed_properties() -> None:
    builder = RecordingBuilder()
    builder.add(ObsKind.CALL, line=10, first_line=10, detail={"nd_names": "random,time"})
    index = StaticIndex(root="/nowhere", elements=[element("m::decide", "decide", "m.py", 10, 20)])
    result = Tracer(index).materialise(make_run(), builder.build())
    observations = result.nondeterminism
    assert len(observations) == 2
    assert {observation.kind for observation in observations} == {
        NondeterminismKind.WALL_CLOCK,
        NondeterminismKind.RANDOMNESS,
    }
    for observation in observations:
        assert observation.element_id == "m::decide"
        assert observation.provenance.method is Method.RUNTIME_OBSERVED
        assert observation.provenance.run_id == "run_001"
        assert observation.event_ids == ("evt_00000001",)
        assert observation.provenance.confidence is Confidence.HEURISTIC


def test_a_nondeterministic_module_executed_outside_the_root_is_recorded() -> None:
    recording = RecordingBuilder().build(external_frames={"<stdlib>/random.py": 4})
    result = Tracer(StaticIndex(root="/nowhere")).materialise(make_run(), recording)
    assert [observation.kind for observation in result.nondeterminism] == [
        NondeterminismKind.RANDOMNESS
    ]
    assert result.nondeterminism[0].provenance.confidence is Confidence.RESOLVED


def test_hash_randomization_is_recorded() -> None:
    recording = RecordingBuilder().build(hash_randomization=True)
    result = Tracer(StaticIndex(root="/nowhere")).materialise(make_run(), recording)
    assert [observation.kind for observation in result.nondeterminism] == [
        NondeterminismKind.HASH_ORDERING
    ]


def test_thread_interleaving_is_recorded_and_the_tracer_order_stays_stable() -> None:
    index = StaticIndex(
        root="/nowhere",
        elements=[
            element("m::decide", "decide", "m.py", 10, 20),
            element("m::worker", "worker", "m.py", 30, 40),
        ],
    )
    def build(interleaved: bool) -> Recording:
        builder = RecordingBuilder()
        main = dict(line=10, first_line=10, qualname="decide", frame_key=1, slot=0)
        worker = dict(line=30, first_line=30, qualname="worker", frame_key=2, slot=1)
        order = (
            [(ObsKind.CALL, main), (ObsKind.CALL, worker), (ObsKind.RETURN, worker),
             (ObsKind.RETURN, main)]
            if interleaved
            else [(ObsKind.CALL, worker), (ObsKind.CALL, main), (ObsKind.RETURN, main),
                  (ObsKind.RETURN, worker)]
        )
        for kind, where in order:
            builder.add(kind, **where)
        return builder.build()

    tracer = Tracer(index)
    first = tracer.materialise(make_run(), build(True))
    second = tracer.materialise(make_run(), build(False))
    assert tracer.events_jsonl(first.events) == tracer.events_jsonl(second.events)
    assert [event.element_id for event in first.events] == [
        "m::decide",
        "m::decide",
        "m::worker",
        "m::worker",
    ]
    kinds = {observation.kind for observation in first.nondeterminism}
    assert NondeterminismKind.THREAD_INTERLEAVING in kinds


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


def test_the_overlay_is_written_deterministically(tmp_path: Path) -> None:
    result = Tracer(decision_index()).materialise(make_run(), branch_recording(13))
    tracer = Tracer(decision_index())
    files = tracer.emit(result, tmp_path / "runtime" / "run_001")
    assert set(files) == EXPECTED_ARTIFACTS
    again = tracer.emit(result, tmp_path / "runtime" / "again")
    assert files == again
    lines = files["events.jsonl"].splitlines()
    assert len(lines) == len(result.events) == 5
    for line in lines:
        payload = json.loads(line)
        assert payload["provenance"]["method"] == "RUNTIME_OBSERVED"
        assert payload["provenance"]["run_id"] == "run_001"
    mapping = json.loads(files["mapping.json"])
    assert mapping["rate_text"] == "5/5 target events mapped (100.0%)"
    assert mapping["external_frames_total"] == 0


def test_recordings_round_trip_unchanged() -> None:
    recording = branch_recording(13)
    text = recording.dumps()
    assert Recording.loads(text).dumps() == text


# ---------------------------------------------------------------------------
# live collection probes
#
# The collector's line handling -- branches, handlers, feature writes -- and
# its live capture path cannot be reached through the fixture corpus today:
# every Mode A case except `run_linear` is still a `def func(): pass`
# placeholder with an empty expectation (card 8). Rather than ship those code
# paths untested, they are exercised against these four probe functions defined
# right here. No fixture and no target code is involved; when card 8 fills the
# corpus in, `test_mode_a_fixture_matches_its_expectation` grades the same
# behaviour against hand-written expectations.
# ---------------------------------------------------------------------------


def _branching_probe(value: int) -> str:
    if value > 5:
        result = "high"
    else:
        result = "low"
    return result


def _raising_probe() -> str:
    try:
        raise ValueError("boom")
    except ValueError:
        return "caught"


def _feature_probe() -> dict[str, int]:
    row: dict[str, int] = {}
    row["score"] = 42
    return row


def _payload_probe(api_key: str, frame: Any) -> Any:
    return frame


def line_of(function: Any, needle: str) -> int:
    lines, start = inspect.getsourcelines(function)
    for offset, text in enumerate(lines):
        if needle in text:
            return start + offset
    raise AssertionError(f"{needle!r} not found in {function.__name__}")


def span_of(function: Any) -> tuple[int, int]:
    lines, start = inspect.getsourcelines(function)
    return start, start + len(lines) - 1


def probe_element(function: Any, element_id: str) -> Element:
    start, end = span_of(function)
    return element(element_id, function.__name__, THIS_FILE, start, end, module="probes")


def collect(index: StaticIndex, call: Any, **kwargs: Any) -> tuple[Tracer, RunRecord, Recording]:
    run = make_run()
    tracer = Tracer(index, **kwargs)
    with tracer.collector(run) as collector:
        call()
    recording = collector.recording()
    tracer.hold(run, recording)
    return tracer, run, recording


def branch_index() -> StaticIndex:
    cond = line_of(_branching_probe, "if value >")
    high = line_of(_branching_probe, '"high"')
    low = line_of(_branching_probe, '"low"')
    return StaticIndex(
        root=str(Path(__file__).resolve().parent),
        elements=[probe_element(_branching_probe, "probes::_branching_probe")],
        cfg_blocks=[
            CFGBlock("pb1", "probes::_branching_probe", BlockKind.BRANCH, SourceSpan(THIS_FILE, cond), AST),
            CFGBlock("pb2", "probes::_branching_probe", BlockKind.NORMAL, SourceSpan(THIS_FILE, high), AST),
            CFGBlock("pb3", "probes::_branching_probe", BlockKind.NORMAL, SourceSpan(THIS_FILE, low), AST),
        ],
        cfg_edges=[
            CFGEdge("pe1", "pb1", "pb2", condition="value > 5", taken_when=True),
            CFGEdge("pe2", "pb1", "pb3", condition="value > 5", taken_when=False),
        ],
        decisions=[
            DecisionPoint(
                id="pd1",
                element_id="probes::_branching_probe",
                condition_source="value > 5",
                reads_ids=("@feature:value",),
                outcomes=(("high", "pb2"), ("low", "pb3")),
                is_sink=True,
            )
        ],
    )


@pytest.mark.parametrize("value,expected", [(7, "high"), (1, "low")])
def test_live_branch_records_the_path_actually_taken(value: int, expected: str) -> None:
    index = branch_index()
    tracer, run, _ = collect(index, lambda: _branching_probe(value))
    result = tracer.result(run)
    branch = next(event for event in result.events if event.kind is EventKind.BRANCH)
    assert branch.branch_taken == expected
    assert branch.values["value"].repr_text == str(value)
    assert branch.element_id == "probes::_branching_probe"
    decision = next(event for event in result.events if event.kind is EventKind.DECISION)
    assert decision.branch_taken == expected


def test_live_exception_is_recorded_with_where_it_was_caught() -> None:
    start, end = span_of(_raising_probe)
    handler = line_of(_raising_probe, "except ValueError")
    index = StaticIndex(
        root=str(Path(__file__).resolve().parent),
        elements=[probe_element(_raising_probe, "probes::_raising_probe")],
        cfg_blocks=[
            CFGBlock(
                "ph1",
                "probes::_raising_probe",
                BlockKind.HANDLER,
                SourceSpan(THIS_FILE, handler, end),
                AST,
            )
        ],
    )
    tracer, run, _ = collect(index, _raising_probe)
    result = tracer.result(run)
    raised = next(event for event in result.events if event.kind is EventKind.EXCEPTION)
    assert raised.element_id == "probes::_raising_probe"
    assert raised.values["exception_type"].repr_text == "'ValueError'"
    assert "boom" in raised.values["exception_message"].repr_text
    assert raised.provenance is not None
    assert "disposition=CAUGHT" in raised.provenance.note
    returned = next(event for event in result.events if event.kind is EventKind.RETURN)
    assert returned.values["return_value"].repr_text == "'caught'"


def test_live_feature_write_is_read_out_of_the_running_frame() -> None:
    write_line = line_of(_feature_probe, '"score"')
    index = StaticIndex(
        root=str(Path(__file__).resolve().parent),
        elements=[probe_element(_feature_probe, "probes::_feature_probe")],
        lineage=[
            LineageEdge(
                id="pl1",
                kind=LineageKind.CONTAINER_WRITE,
                source_id="probes::_feature_probe",
                target_id="@feature:score",
                provenance=AST,
                span=SourceSpan(THIS_FILE, write_line),
            )
        ],
    )
    tracer, run, _ = collect(index, _feature_probe)
    write = next(
        event for event in tracer.result(run).events if event.kind is EventKind.FEATURE_WRITE
    )
    assert write.element_id == "@feature:score"
    assert write.values["score"].repr_text == "42"


def test_live_capture_redacts_and_summarizes_at_the_moment_of_capture() -> None:
    index = StaticIndex(
        root=str(Path(__file__).resolve().parent),
        elements=[probe_element(_payload_probe, "probes::_payload_probe")],
    )
    frame = FakeFrame(1_000_000)
    tracer, run, recording = collect(index, lambda: _payload_probe("s3cret", frame))
    call = next(
        event for event in tracer.result(run).events if event.kind is EventKind.CALL
    )
    assert call.values["api_key"].status is CaptureStatus.REDACTED
    assert call.values["frame"].status is CaptureStatus.SUMMARIZED
    assert call.values["frame"].shape == "(1000000, 3)"
    assert call.values["frame"].original_size == 24_000_000
    assert "s3cret" not in recording.dumps()
    assert len(recording.dumps()) < 20_000


# ---------------------------------------------------------------------------
# contract and corpus conformance
# ---------------------------------------------------------------------------

#: The `run_*` cases FIXTURES.md specifies. Each is graded by
#: `test_mode_a_fixture_matches_its_expectation` as soon as it declares events.
FIXTURES_RUN_CASES = (
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

#: Cases that are still `def func(): pass` with an empty expectation. They are
#: covered here by constructed recordings and live probes instead. When card 8
#: fills one in, this test fails so the real fixture is graded rather than
#: quietly skipped.
PLACEHOLDER_CASES = (
    "run_branching",
    "run_contradiction",
    "run_exception",
    "run_large_frame",
    "run_nondeterministic",
    "run_redaction",
    "run_replay",
    "run_unmapped",
    "run_values",
)


def test_every_fixtures_run_case_exists() -> None:
    missing = [name for name in FIXTURES_RUN_CASES if not (MODE_A / name).is_dir()]
    assert not missing, f"FIXTURES.md Mode A cases with no fixture: {missing}"


def test_placeholder_cases_are_named_not_hidden() -> None:
    filled = [
        name
        for name in PLACEHOLDER_CASES
        if json.loads((MODE_A / name / "expected.json").read_text(encoding="utf-8")).get(
            "events"
        )
    ]
    assert not filled, (
        "these Mode A fixtures now declare expected events and are graded directly by "
        f"test_mode_a_fixture_matches_its_expectation; drop them from PLACEHOLDER_CASES: {filled}"
    )


def test_the_controls_card_eleven_reports_satisfy_the_tracer() -> None:
    """Card 11's harness names its controls network/filesystem/process."""
    run = make_run(
        controls={
            "network": True,
            "filesystem": True,
            "process": True,
            "environment": True,
            "external_clients": True,
        }
    )
    assert refusal_reason(run) == ""
    TraceCollector(run, StaticIndex(root="/nowhere"))


def test_emitted_events_match_the_trace_event_schema(tmp_path: Path) -> None:
    schema = json.loads(
        (REPO_ROOT / "src" / "cascade_map" / "contracts" / "schema.json").read_text(
            encoding="utf-8"
        )
    )
    definition = schema["$defs"]["TraceEvent"]
    assert schema["artifacts"]["runtime/events.jsonl"]["sortKey"] == "event_id"
    result = Tracer(decision_index()).materialise(make_run(), branch_recording(13))
    text = Tracer(decision_index()).emit(result, tmp_path)["events.jsonl"]
    assert text.endswith("\n")
    lines = text.splitlines()
    assert len(lines) == len(result.events) == 5
    for line in lines:
        payload = json.loads(line)
        assert set(payload) <= set(definition["properties"])
        for field in definition["required"]:
            assert field in payload, field
        assert isinstance(payload["sequence"], int)
        for capture in payload["values"].values():
            assert capture["status"] in ("FULL", "SUMMARIZED", "REDACTED", "DROPPED")
            if capture["status"] != "FULL":
                assert capture["reason"]


# ---------------------------------------------------------------------------
# the RunObserver seam: what card 11 starts and stops around the target call
# ---------------------------------------------------------------------------


def observed_window(tracer: Tracer, run: RunRecord, call: Any) -> BaseException | None:
    """Mimic card 11's window: start inside it, stop in the `finally`."""
    raised: BaseException | None = None
    tracer.start(run)
    try:
        call()
    except BaseException as exc:  # noqa: BLE001 - the harness swallows this too
        raised = exc
    finally:
        tracer.stop()
    return raised


def test_the_tracer_is_a_run_observer() -> None:
    observer: RunObserver = Tracer(StaticIndex(root="/nowhere"))
    members = {name for name in vars(RunObserver) if not name.startswith("_")}
    assert members == {"start", "stop"}
    for name in sorted(members):
        assert callable(getattr(observer, name))
        assert inspect.signature(getattr(Tracer, name)) == inspect.signature(
            getattr(RunObserver, name)
        ), name


def test_observing_a_run_holds_its_recording_without_touching_disk() -> None:
    index = branch_index()
    tracer = Tracer(index)
    assert tracer.recordings_dir is None
    observed_window(tracer, make_run(), lambda: _branching_probe(7))
    result = tracer.result(make_run())
    assert len(result.events) >= 3
    branch = next(event for event in result.events if event.kind is EventKind.BRANCH)
    assert branch.branch_taken == "high"
    with pytest.raises(TraceRefused):
        tracer.recording_path("run_001")  # no directory configured, and none needed


def test_the_observer_stops_even_when_the_target_raises() -> None:
    index = StaticIndex(
        root=str(Path(__file__).resolve().parent),
        elements=[probe_element(_raising_probe, "probes::_raising_probe")],
    )
    tracer = Tracer(index)

    def boom() -> None:
        _raising_probe()
        raise KeyError("the scenario failed")

    raised = observed_window(tracer, make_run(), boom)
    assert isinstance(raised, KeyError)
    assert sys.gettrace() is None, "the trace hook must not outlive the window"
    events = tracer.result(make_run()).events
    assert [event.kind for event in events].count(EventKind.EXCEPTION) >= 1


def test_the_observer_refuses_a_run_it_may_not_trace() -> None:
    tracer = Tracer(StaticIndex(root="/nowhere"))
    run = make_run(
        controls={
            "network": False,
            "filesystem": True,
            "process": True,
        }
    )
    with pytest.raises(TraceRefused) as raised:
        tracer.start(run)
    assert "'network'" in raised.value.reason
    assert sys.gettrace() is None, "a refusal must install nothing"
    tracer.stop()  # safe after a refusal
    with pytest.raises(TraceRefused):
        tracer.result(run)


def test_stopping_an_observer_that_never_started_is_a_no_op() -> None:
    tracer = Tracer(StaticIndex(root="/nowhere"))
    tracer.stop()
    tracer.stop()
    assert sys.gettrace() is None


def test_an_observer_cannot_be_started_twice_but_can_observe_twice() -> None:
    index = branch_index()
    tracer = Tracer(index)
    tracer.start(make_run("run_a"))
    try:
        with pytest.raises(RuntimeError):
            tracer.start(make_run("run_b"))
    finally:
        tracer.stop()
    observed_window(tracer, make_run("run_b"), lambda: _branching_probe(1))
    assert tracer.result(make_run("run_a")).events
    second = tracer.result(make_run("run_b"))
    assert next(
        event for event in second.events if event.kind is EventKind.BRANCH
    ).branch_taken == "low"


def test_an_empty_trace_never_reports_a_perfect_mapping_rate() -> None:
    tracer = Tracer(StaticIndex(root="/nowhere"))
    observed_window(tracer, make_run(), lambda: None)
    report = tracer.result(make_run()).mapping
    assert report.total_events == 0
    assert report.rate_permille == 0
    assert report.rate_text.startswith(
        "0/0 target events mapped: nothing of the target was observed in this run"
    )


def test_the_recording_carries_the_escape_paths_the_harness_could_not_close() -> None:
    run = RunRecord(
        run_id="run_001",
        target_hashes={},
        graph_hash="g",
        scenario="fixture",
        interpreter="cpython",
        controls_active={"network": True, "filesystem": True, "process": True},
        blocked=(),
        unguaranteed=("direct _posixsubprocess.fork_exec",),
    )
    tracer = Tracer(StaticIndex(root="/nowhere"))
    observed_window(tracer, run, lambda: None)
    assert tracer.load_recording(run).header["unguaranteed"] == [
        "direct _posixsubprocess.fork_exec"
    ]


# ---------------------------------------------------------------------------
# persistence: the child process dies, the file is what survives
# ---------------------------------------------------------------------------


def test_stopping_persists_the_recording_when_a_directory_is_configured(
    tmp_path: Path,
) -> None:
    tracer = Tracer(branch_index(), recordings_dir=tmp_path)
    run = make_run()
    observed_window(tracer, run, lambda: _branching_probe(7))
    path = tracer.recording_path(run.run_id)
    assert path.is_file(), "cascade-map trace has nothing else to read"
    assert Recording.read(path).observations
    assert tracer.result(run).mapping.persist_error == ""


def test_materialising_from_disk_and_from_memory_are_byte_identical(
    tmp_path: Path,
) -> None:
    index = branch_index()
    held = Tracer(index, recordings_dir=tmp_path)
    run = make_run()
    observed_window(held, run, lambda: _branching_probe(7))

    from_disk = Tracer(index, recordings_dir=tmp_path)
    assert not from_disk._recordings, "this tracer must read the file, not a held copy"
    memory_result = held.result(run)
    disk_result = from_disk.result(run)
    assert memory_result.events
    memory_files = held.emit(memory_result, tmp_path / "memory")
    disk_files = from_disk.emit(disk_result, tmp_path / "disk")
    assert memory_files == disk_files
    assert set(memory_files) == EXPECTED_ARTIFACTS
    for name in sorted(memory_files):
        assert (tmp_path / "memory" / name).read_bytes() == (
            tmp_path / "disk" / name
        ).read_bytes()
    assert Recording.read(tracer_path := held.recording_path(run.run_id)).dumps() == (
        tracer_path.read_text(encoding="utf-8")
    )


def test_a_refused_run_persists_nothing(tmp_path: Path) -> None:
    tracer = Tracer(StaticIndex(root="/nowhere"), recordings_dir=tmp_path)
    run = make_run(controls={"network": False, "filesystem": True, "process": True})
    with pytest.raises(TraceRefused):
        tracer.start(run)
    tracer.stop()
    assert not tracer.recording_path(run.run_id).exists()
    assert list(tmp_path.iterdir()) == [], "an empty file would read as a run that observed nothing"


def test_a_blocked_write_is_recorded_not_raised(tmp_path: Path) -> None:
    """The write happens inside the sandbox window, where it may be denied.

    Card 11's `BlockedOperation` is not imported here -- the tracer must not
    depend on the harness -- so the denial is staged with a real filesystem
    refusal: a plain file where the recordings directory should be.
    """
    blocked = tmp_path / "denied"
    blocked.write_text("not a directory", encoding="utf-8")
    tracer = Tracer(branch_index(), recordings_dir=blocked)
    run = make_run()

    raised = observed_window(tracer, run, lambda: _branching_probe(7))
    assert raised is None, "an observer that cannot save must not break the run"

    result = tracer.result(run)  # the held copy still works
    assert result.events
    assert "run_001/recording.jsonl" in result.mapping.persist_error
    assert "cannot be replayed later" in result.mapping.persist_error
    assert "Error" in result.mapping.persist_error
    assert blocked.read_text(encoding="utf-8") == "not a directory"
    assert json.loads(tracer.emit(result, tmp_path / "out")["mapping.json"])[
        "persist_error"
    ] == result.mapping.persist_error


# ---------------------------------------------------------------------------
# determinism: an address is not information
# ---------------------------------------------------------------------------


class Widget:
    """No `__repr__`, so CPython renders `<Widget object at 0x...>`."""


def test_two_objects_at_different_addresses_capture_identically() -> None:
    first, second = Widget(), Widget()
    assert id(first) != id(second), "two live objects cannot share an address"
    one = capture_value("widget", first)
    two = capture_value("widget", second)
    assert one == two
    assert one.repr_text.endswith("object at 0x...>")
    assert "0x..." in one.repr_text
    assert one.type_name == "Widget", "the useful half of the repr survives"
    assert ADDRESS_NOTE in one.reason


def test_addresses_are_normalised_wherever_they_are_rendered() -> None:
    widget = Widget()
    cases = {
        "plain": widget,
        "nested": {"inner": [widget]},
        "exception": ValueError(widget),
        "tuple": (widget, widget),
    }
    assert len(cases) == 4
    for name, value in cases.items():
        text = capture_value(name, value).repr_text
        assert "0x" not in text or "0x..." in text, (name, text)
        assert not re.search(r"0x[0-9a-fA-F]{6,}", text), (name, text)


def test_a_decimal_identity_is_normalised_too() -> None:
    """`importlib._bootstrap._ModuleLock` reprs `id(self)` in decimal."""

    class Lock:
        def __repr__(self) -> str:
            return f"_ModuleLock('run_linear') at {id(self)}"

    first, second = Lock(), Lock()
    assert repr(first) != repr(second)
    one, two = capture_value("lock", first), capture_value("lock", second)
    assert one == two
    assert one.repr_text == "_ModuleLock('run_linear') at ..."
    assert ADDRESS_NOTE in one.reason


def test_the_digit_floor_keeps_small_real_numbers() -> None:
    """The one narrowing: six digits or more, so a real counter survives."""
    assert stable_text("retry at 3") == "retry at 3"
    assert stable_text("due at 1500") == "due at 1500"
    assert stable_text("step at 99999") == "step at 99999"
    assert stable_text("lock at 100000") == "lock at ..."
    assert stable_text("lock at 140264054584784") == "lock at ..."


def test_a_hex_value_that_is_real_data_survives() -> None:
    """Only CPython's own `at 0x...` form is touched."""
    assert capture_value("digest", "0xdeadbeef").repr_text == "'0xdeadbeef'"
    assert capture_value("digest", "0xdeadbeef").reason == ""
    assert capture_value("ids", ["0xcafef00d"]).repr_text == "['0xcafef00d']"
    assert stable_text("checksum 0xdeadbeef") == "checksum 0xdeadbeef"
    assert stable_text("<X object at 0xdeadbeef>") == "<X object at 0x...>"


#: Run in subprocesses so PYTHONHASHSEED actually differs -- and so the two
#: runs allocate at different addresses, which is what the phase gate caught
#: and what two in-process materialisations of one recording cannot show.
_DETERMINISM_PROBE = '''
import hashlib, json, sys
sys.path.insert(0, {src!r})
from cascade_map.contracts.interfaces import (
    Confidence, Element, ElementKind, Method, Provenance, RunRecord, SourceSpan,
)
from cascade_map.tracer import StaticIndex, Tracer

sys.path.insert(0, {probe_dir!r})
sys.path.insert(0, {fixtures!r})
import probe_module

prov = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN)


def element(eid, qualname, path, line, end, kind=ElementKind.FUNCTION, module="m"):
    return Element(
        id=eid, kind=kind, name=qualname or module, qualname=qualname, module=module,
        span=SourceSpan(path=path, line=line, end_line=end), provenance=prov,
        content_hash="h",
    )


index = StaticIndex(
    root={probe_dir!r},
    elements=[
        element("probe::handle", "handle", "probe_module.py", 4, 6),
        element("probe::Widget", "Widget", "probe_module.py", 1, 2,
                kind=ElementKind.CLASS),
    ],
    sink_element_ids=["probe::handle"],
)
run = RunRecord(
    run_id="run_001", target_hashes={{"probe_module.py": "h"}}, graph_hash="g",
    scenario="determinism", interpreter="cpython",
    controls_active={{"network": True, "filesystem": True, "process": True}},
    blocked=(),
)
tracer = Tracer(index, recordings_dir={out!r} + "/recordings")
tracer.start(run)
try:
    probe_module.handle(probe_module.Widget(), {{"gamma", "alpha", "beta"}})
    probe_module.handle(probe_module.Lock(), {{"delta", "epsilon"}})
    import run_linear  # the real import path, inside the trace window
    run_linear.main()
finally:
    tracer.stop()

result = tracer.result(run)
files = tracer.emit(result, {out!r} + "/runtime/run_001")
digests = {{
    name: hashlib.sha256(text.encode()).hexdigest() for name, text in sorted(files.items())
}}
digests["recording"] = hashlib.sha256(
    tracer.recording_path("run_001").read_bytes()
).hexdigest()
print(json.dumps({{
    "digests": digests,
    "events": len(result.events),
    "captured": sum(len(event.values) for event in result.events),
    "addresses": sum(
        1
        for event in result.events
        for capture in event.values.values()
        if "0x..." in capture.repr_text
    ),
    "decimal_ids": sum(
        1
        for event in result.events
        for capture in event.values.values()
        if "_ModuleLock('probe') at ..." in capture.repr_text
    ),
    "rate": result.mapping.rate_text,
    "nondet_ids": [observation.id for observation in result.nondeterminism],
}}))
'''

_PROBE_MODULE = '''class Widget:
    """No __repr__: CPython renders the hex form."""


class Lock:
    """Reprs its identity in decimal, as importlib's _ModuleLock does."""

    def __repr__(self):
        return "_ModuleLock('probe') at %s" % id(self)


def handle(widget, names):
    seen = sorted(names)
    return {"widget": widget, "names": seen, "count": len(seen)}
'''


def test_output_is_byte_identical_across_processes_and_hash_seeds(tmp_path: Path) -> None:
    """The phase gate's own check: same scenario, same output root, twice."""
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    (probe_dir / "probe_module.py").write_text(_PROBE_MODULE, encoding="utf-8")
    out = tmp_path / "out"
    script = _DETERMINISM_PROBE.format(
        src=str(REPO_ROOT / "src"),
        probe_dir=str(probe_dir),
        fixtures=str(MODE_A),
        out=str(out),
    )
    results = []
    for seed in ("0", "1", "524287"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1"),
            cwd=str(REPO_ROOT),
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        results.append(json.loads(completed.stdout))

    assert len(results) == 3
    assert results[0]["events"] >= 6, results[0]
    assert results[0]["captured"] >= 8, results[0]
    assert results[0]["addresses"] >= 1, "nothing with a hex address was captured"
    assert results[0]["decimal_ids"] >= 1, "nothing with a decimal id was captured"
    empty_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert results[0]["digests"]["events.jsonl"] != empty_digest, "identical emptiness"
    assert results[0]["digests"]["recording"] != empty_digest
    assert set(results[0]["digests"]) == EXPECTED_ARTIFACTS | {"recording"}
    # What must never vary: the trace itself.
    graded = {"events.jsonl", "contradictions.jsonl", "mapping.json"}
    for later in results[1:]:
        for name in sorted(graded):
            assert later["digests"][name] == results[0]["digests"][name], name
        assert later["rate"] == results[0]["rate"]

    # What legitimately varies, and only this: PYTHONHASHSEED=0 disables hash
    # randomization, so the run genuinely has one fewer nondeterminism source.
    # It is recorded rather than hidden, which is the whole point -- so the
    # record differs, and the two randomized seeds agree with each other
    # exactly, recording and all.
    unseeded, *randomized = results
    assert unseeded["nondet_ids"] == []
    assert all("HASH_ORDERING" in "".join(run["nondet_ids"]) for run in randomized)
    assert len(randomized) == 2
    assert randomized[0]["digests"] == randomized[1]["digests"], randomized


# ---------------------------------------------------------------------------
# external is not unmapped
# ---------------------------------------------------------------------------


def test_frozen_and_stdlib_code_is_external_not_dynamic() -> None:
    index = StaticIndex(root="/nowhere")
    frozen = index.relocate("<frozen importlib._bootstrap>")
    assert frozen.path == "<frozen>/importlib._bootstrap"
    assert not frozen.synthetic, "a frozen stdlib module is not dynamically created code"
    assert not frozen.under_root
    assert index.relocate("/usr/lib/python3.11/random.py").path == "<stdlib>/random.py"
    generated = index.relocate("<string>")
    assert generated.synthetic, "exec'd source is still a finding about the static map"


def test_the_import_machinery_does_not_drown_the_mapping_rate() -> None:
    """272 importlib events under 9 target events is not a mapping rate."""
    sys.dont_write_bytecode = True
    index = branch_index()
    tracer = Tracer(index)
    run = make_run()

    def scenario() -> None:
        importlib.import_module("colorsys")  # a real import, inside the window
        _branching_probe(7)

    observed_window(tracer, run, scenario)
    result = tracer.result(run)
    assert result.events
    unmapped = [event for event in result.events if event.kind is EventKind.UNMAPPED]
    # The only unmapped events are this file's own nested `scenario`, which is
    # under the index root and not in the index -- a real "should have been in
    # the static map" finding. None of importlib's 100+ frames are among them.
    assert len(unmapped) <= 2, [event.provenance.note for event in unmapped]
    for event in unmapped:
        assert event.provenance is not None
        assert event.provenance.span is not None
        assert not event.provenance.span.path.startswith(("<frozen", "<stdlib"))
    assert result.mapping.unmapped_reasons.get("DYNAMIC_CODE", 0) == 0
    assert result.mapping.external_frames_total >= 20, "the machinery must be counted"
    frozen = [name for name in result.mapping.external_frames if name.startswith("<frozen>/")]
    assert frozen, result.mapping.external_frames
    assert result.mapping.rate_text.endswith(
        f"{result.mapping.external_frames_total} external frames skipped"
    )
    assert "target events mapped" in result.mapping.rate_text


def test_external_frames_are_recorded_with_where_they_came_from() -> None:
    """Constraint 3: not counted against the rate, but never disappeared."""
    sys.dont_write_bytecode = True
    tracer = Tracer(branch_index())
    run = make_run()
    observed_window(tracer, run, lambda: importlib.import_module("wave"))
    report = tracer.result(run).mapping
    assert report.external_frames, "external frames must be countable and locatable"
    assert sum(report.external_frames.values()) == report.external_frames_total
    assert all(
        name.startswith(
            ("<frozen>/", "<stdlib>/", "<site-packages>/", "<dist-packages>/", "<external>/")
        )
        for name in report.external_frames
    )
    assert json.loads(
        Tracer(branch_index()).emit(tracer.result(run), Path(tempfile.mkdtemp()))["mapping.json"]
    )["external_frames"] == report.external_frames
