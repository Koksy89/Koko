"""Cards 13 and 11, made reachable: intents on the command line, and client
stubs an owner can declare in JSON.

Both capabilities were fully built, tested and verified, and no command
reached either. `IntentRegistry`, `propose_intents`, the checks, the coverage
and `verdicts_jsonl` all existed while nothing wrote `intents.jsonl`;
`RunConfig.client_stubs` was typed and tested while the scenarios file had no
way to express a Python callable, so through the CLI every external client was
simply undeclared.

What this module tests is the WIRING, end to end, through `cli.main` and
`cli.analyze`/`cli.trace` -- not the engine, which `test_alignment.py` covers,
and not the sandbox, which `test_harness.py` covers.

**Nothing here writes into `tests/fixtures/`.** Every program executed is
copied into `tmp_path` first, and the corpus is hashed before and after this
module runs. Mode A runs go through `cli.trace`, which spawns its own child
interpreter, so the sandbox's permanent enforcement flag never lands in this
pytest process.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cascade_map import cli
from cascade_map.alignment import (
    AlignmentEngine,
    load_registry,
    parse_registry_text,
    propose_intents,
    registry_text,
)
from cascade_map.contracts.interfaces import (
    Confidence,
    Method,
    Provenance,
    Reachability,
    ReachabilityState,
    Verdict,
)
from cascade_map.harness.stubs import (
    STUB_KINDS,
    StubDeclarationError,
    declaration_fingerprint,
    parse_declarations,
)

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
MODE_A = FIXTURES_ROOT / "mode_a"
WIRE_INTENTS = MODE_A / "wire_intents"
WIRE_STUBS = MODE_A / "wire_stubs"
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The corpus is read, never written.
# ---------------------------------------------------------------------------


def _snapshot_fixtures_tree() -> dict[str, str]:
    return {
        path.relative_to(FIXTURES_ROOT).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(FIXTURES_ROOT.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module", autouse=True)
def _fixtures_untouched():
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    before = _snapshot_fixtures_tree()
    yield
    sys.dont_write_bytecode = original
    after = _snapshot_fixtures_tree()
    assert after == before, "tests/fixtures/ changed while this module ran"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_case(case: Path, tmp_path: Path, name: str = "") -> tuple[Path, Path]:
    """A fixture case copied into tmp_path: `(target_root, specs_dir)`.

    The copy is what makes "never write into tests/fixtures/" true rather than
    hoped for: the harness redirects the target's own writes into its sandbox,
    but `analyze` writes a cache and Python writes bytecode, and neither
    belongs in the corpus.

    The copied root IS the package, so element ids read `wire_intents.engine::score`
    -- the ids the fixture's own intents file names. Everything that is not
    Python is moved OUT of the target into a sibling: an owner's intents file
    is an input to this tool, not part of the code under analysis, and leaving
    it inside the tree would have the analysis inventory its own inputs.
    """
    root = tmp_path / (name or case.name)
    shutil.copytree(case, root)
    specs = tmp_path / f"specs-{root.name}"
    specs.mkdir(parents=True, exist_ok=True)
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix != ".py":
            shutil.move(str(path), specs / path.name)
    return root, specs


def _island(html: str) -> dict[str, Any]:
    """The blueprint's data island, the same inverse the browser applies."""
    from cascade_map.viewer.blueprint import _unpack_data

    marker = 'id="cascade-blueprint-data">'
    assert marker in html, "page carries no uncompressed data island"
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    return _unpack_data(json.loads(html[start:end]))


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _verdicts_by_element(path: Path) -> dict[str, dict[str, Any]]:
    return {row["element_id"]: row for row in _rows(path)}


def _analyse(
    tmp_path: Path,
    *,
    intents: str = "intents.yaml",
    out: str = "graph",
    sink: str = "wire_intents.engine::decide",
) -> tuple[Path, Path, dict[str, Any]]:
    target, specs = _copy_case(WIRE_INTENTS, tmp_path / "t")
    graph = tmp_path / out
    spec = specs / intents if intents else None
    code, summary = cli.analyze(
        target,
        graph,
        strict_gate=False,
        sink_ids=(sink,) if sink else (),
        intents_path=spec,
    )
    assert code in (cli.EXIT_OK, cli.EXIT_GATE_FAILED), summary
    return target, graph, summary


# ---------------------------------------------------------------------------
# `analyze --intents` writes intents.jsonl and static verdicts
# ---------------------------------------------------------------------------


def test_analyze_writes_intents_jsonl_from_the_loaded_registry(tmp_path: Path) -> None:
    """The file the viewer already knew how to read, and nothing wrote."""
    _target, graph, summary = _analyse(tmp_path)
    rows = _rows(graph / "intents.jsonl")
    assert rows, "intents.jsonl must hold the owner's intents, not be empty"
    assert {row["element_id"] for row in rows} == {
        "wire_intents.engine::helper_on_path",
        "wire_intents.engine::score",
        "wire_intents.engine::gate",
        "wire_intents.engine::declared_live_strategy",
        "wire_intents.engine::never_run",
        "wire_intents.engine::decide",
    }
    by_element = {row["element_id"]: row for row in rows}
    assert by_element["wire_intents.engine::score"]["status"] == "CONFIRMED"
    assert by_element["wire_intents.engine::decide"]["status"] == "PROPOSED"
    assert summary["intents"]["confirmed"] == 5
    assert summary["intents"]["proposed"] == 1


def test_no_intents_file_still_writes_the_artifact_and_says_so(tmp_path: Path) -> None:
    """An absent intents.jsonl and an empty one are different facts."""
    _target, graph, summary = _analyse(tmp_path, intents="")
    assert (graph / "intents.jsonl").is_file()
    assert _rows(graph / "intents.jsonl") == []
    assert summary["intents"]["present"] is False
    report = cli._report(summary, graph, cli.EXIT_OK)
    assert "no spec declared" in report
    assert "NO_INTENT is a reported state" in report


def test_an_unreachable_declared_live_element_is_misaligned(tmp_path: Path) -> None:
    """The owner's actual question, answered against their own declaration.

    `declared_live_strategy` is complete, correct and importable. Dead-code
    detection guesses at it from structure; this says it against a sentence
    the owner wrote, with the reachability record as the evidence.
    """
    _target, graph, _summary = _analyse(tmp_path)
    verdicts = _verdicts_by_element(graph / "verdicts.jsonl")
    verdict = verdicts["wire_intents.engine::declared_live_strategy"]
    assert verdict["verdict"] == Verdict.MISALIGNED.value
    assert "NO_SINK_PATH" in verdict["observation"]
    assert "built but never plugged in" in verdict["observation"]
    assert verdict["evidence_ids"], "a verdict with no evidence is an opinion"
    assert any(
        evidence.startswith("reach:") or "reach" in evidence
        for evidence in verdict["evidence_ids"]
    ), verdict["evidence_ids"]
    assert verdict["provenance"]["method"] == Method.STRUCTURAL_MATCH.value


def test_static_verdicts_cover_each_kind_the_static_map_can_reach(
    tmp_path: Path,
) -> None:
    _target, graph, _summary = _analyse(tmp_path)
    verdicts = _verdicts_by_element(graph / "verdicts.jsonl")
    assert (
        verdicts["wire_intents.engine::helper_on_path"]["verdict"]
        == Verdict.ALIGNED.value
    ), "`reaches decision` is settled by the static map, with nothing executed"
    # `score` also declares a VALUE expectation, which no static evidence can
    # settle. One unchecked expectation is enough to keep it out of ALIGNED.
    score = verdicts["wire_intents.engine::score"]
    assert score["verdict"] == Verdict.UNVERIFIABLE.value
    assert "nothing was executed" in score["observation"]
    assert (
        verdicts["wire_intents.engine::declared_live_strategy"]["verdict"]
        == Verdict.MISALIGNED.value
    )
    # A value invariant cannot be settled without a run, and the static
    # verdict says exactly that instead of reporting it as met.
    gate = verdicts["wire_intents.engine::gate"]
    assert gate["verdict"] == Verdict.UNVERIFIABLE.value
    assert "nothing was executed" in gate["observation"]
    # A PROPOSED intent never grounds ALIGNED or MISALIGNED.
    decide = verdicts["wire_intents.engine::decide"]
    assert decide["verdict"] == Verdict.UNVERIFIABLE.value
    assert "PROPOSED" in decide["observation"]


def test_static_verdicts_never_claim_a_run_happened(tmp_path: Path) -> None:
    """Nothing in `analyze` executes, so no static verdict may be
    RUNTIME_OBSERVED, carry a run id, or carry event ids."""
    _target, graph, _summary = _analyse(tmp_path)
    rows = _rows(graph / "verdicts.jsonl")
    assert len(rows) == 6, "a loop over nothing proves nothing"
    for row in rows:
        provenance = row["provenance"]
        assert provenance["method"] != Method.RUNTIME_OBSERVED.value
        assert not provenance.get("run_id")
        assert not provenance.get("event_ids")
        assert row["verdict"] != Verdict.NOT_EXERCISED.value, (
            "NOT_EXERCISED is a fact about a RUN. In mode 1 it is true of "
            "every element and says nothing."
        )


def test_the_verdict_scope_is_disclosed_in_the_manifest(tmp_path: Path) -> None:
    """verdicts.jsonl covers the registry's elements, not the target's. A
    scoped artifact that reads as exhaustive is the mistake the slice scope
    disclosure exists to prevent."""
    _target, graph, _summary = _analyse(tmp_path)
    manifest = json.loads((graph / "manifest.json").read_text(encoding="utf-8"))
    disclosure = manifest["intents"]
    assert disclosure["intents"] == 6
    assert disclosure["elements_judged"] == len(_rows(graph / "verdicts.jsonl"))
    assert "not every element" in disclosure["note"]
    elements = len(_rows(graph / "elements.jsonl"))
    assert disclosure["elements_judged"] < elements


# ---------------------------------------------------------------------------
# A malformed intents file is LOUD
# ---------------------------------------------------------------------------


def test_every_problem_in_the_intents_file_reaches_unresolved_with_its_line(
    tmp_path: Path,
) -> None:
    """The worst possible outcome is an intents file that silently did
    nothing. Every problem carries the path, the line and the reason."""
    _target, graph, summary = _analyse(tmp_path, intents="intents_malformed.yaml")
    issues = summary["intents"]["issues"]
    assert len(issues) >= 3, issues
    reasons = {issue["reason"] for issue in issues}
    assert "MISSING_TARGET" in reasons, "an intent naming no element does nothing"
    assert "AMBIGUOUS" in reasons, "two intents for one element is not resolvable"
    for issue in issues:
        assert issue["line"] > 0, issue
        assert issue["description"], issue

    # And they are in unresolved.jsonl, with a span, like everything else this
    # run could not resolve.
    unresolved = [
        row
        for row in _rows(graph / "unresolved.jsonl")
        if row["id"].startswith("@intent-issue:")
    ]
    assert len(unresolved) == len(issues)
    for row in unresolved:
        assert row["span"]["line"] > 0
        assert row["span"]["path"].endswith("intents_malformed.yaml")


def test_the_report_prints_every_intents_problem_with_its_line(
    tmp_path: Path,
) -> None:
    _target, graph, summary = _analyse(tmp_path, intents="intents_malformed.yaml")
    report = cli._report(summary, graph, cli.EXIT_OK)
    assert "PROBLEM(S) IN YOUR INTENTS FILE" in report
    assert "intents_malformed.yaml:" in report
    assert "names an element that does not exist" in report
    # The unparseable invariant is counted, not quietly checked one
    # expectation short of what the owner wrote.
    assert "not in the checkable language" in report


def test_an_entry_with_no_status_is_never_read_as_confirmation(
    tmp_path: Path,
) -> None:
    _target, graph, summary = _analyse(tmp_path, intents="intents_malformed.yaml")
    rows = {row["element_id"]: row for row in _rows(graph / "intents.jsonl")}
    assert rows, "the malformed file still yields the entries it could read"
    entry = rows.get("wire_intents.engine::score")
    if entry is not None:
        assert entry["status"] != "CONFIRMED", (
            "an entry with no `status:` was read as owner confirmation"
        )
    else:
        # Rejected outright is also correct, and then the element must be
        # UNVERIFIABLE rather than NO_INTENT: the owner DID write something.
        verdicts = _verdicts_by_element(graph / "verdicts.jsonl")
        assert (
            verdicts["wire_intents.engine::score"]["verdict"]
            == Verdict.UNVERIFIABLE.value
        )
    assert any(
        issue["reason"] in ("SYNTAX_ERROR", "UNSUPPORTED_SYNTAX", "MISSING_TARGET")
        for issue in summary["intents"]["issues"]
    )


def test_a_duplicated_element_is_unverifiable_not_arbitrated(
    tmp_path: Path,
) -> None:
    _target, graph, _summary = _analyse(tmp_path, intents="intents_malformed.yaml")
    verdicts = _verdicts_by_element(graph / "verdicts.jsonl")
    gate = verdicts["wire_intents.engine::gate"]
    assert gate["verdict"] == Verdict.UNVERIFIABLE.value
    assert "would be a guess" in gate["observation"]


def test_a_named_intents_file_that_does_not_exist_is_a_usage_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Discovered before the analysis, not in the report after it."""
    target, _specs = _copy_case(WIRE_INTENTS, tmp_path / "t")
    code = cli.main(
        [
            "analyze",
            str(target),
            "--out",
            str(tmp_path / "graph"),
            "--no-gate",
            "--no-progress",
            "--intents",
            str(tmp_path / "nowhere.yaml"),
        ]
    )
    assert code == cli.EXIT_USAGE
    printed = capsys.readouterr()
    assert "no intents file at" in printed.err
    assert "Nothing was analysed" in printed.err
    assert not (tmp_path / "graph").exists()


def test_a_settings_intents_path_that_does_not_exist_is_reported_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INTENTS naming a file that is not there must never read as 'no spec'."""
    target, _specs = _copy_case(WIRE_INTENTS, tmp_path / "t")
    graph = tmp_path / "graph"
    _code, summary = cli.analyze(
        target,
        graph,
        strict_gate=False,
        intents_path=tmp_path / "gone.yaml",
    )
    assert summary["intents"]["present"] is False
    assert summary["intents"]["issues"], "a missing named file is a reported issue"
    report = cli._report(summary, graph, cli.EXIT_OK)
    assert "THE FILE YOU NAMED WAS NOT READ" in report


# ---------------------------------------------------------------------------
# --propose-intents
# ---------------------------------------------------------------------------


def test_propose_intents_writes_a_starter_registry_marked_proposed(
    tmp_path: Path,
) -> None:
    target, _specs = _copy_case(WIRE_INTENTS, tmp_path / "t")
    out = tmp_path / "proposed.yaml"
    code = cli.main(
        [
            "analyze",
            str(target),
            "--out",
            str(tmp_path / "graph"),
            "--no-gate",
            "--no-progress",
            "--propose-intents",
            str(out),
        ]
    )
    assert code == cli.EXIT_OK
    text = out.read_text(encoding="utf-8")
    entries = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("status:")
    ]
    assert entries, "the starter file has no entries at all"
    assert set(entries) == {"status: PROPOSED"}, (
        "the tool proposes and the owner confirms: no entry may be written "
        "CONFIRMED"
    )
    assert len(entries) >= 5
    assert "NOT owner-confirmed" in text


def test_a_proposed_registry_round_trips_through_the_parser(tmp_path: Path) -> None:
    """A starter file the tool's own parser cannot read back is not a starter
    file. This is the general test: it round-trips every proposal the whole
    Mode A corpus produces, not the one case that prompted it."""
    from cascade_map.ingest.inventory import Ingestor

    checked = 0
    for case in sorted(MODE_A.iterdir()):
        if not case.is_dir():
            continue
        root, _specs = _copy_case(case, tmp_path / f"rt-{case.name}")
        elements, _unresolved = Ingestor().inventory(str(root))
        proposals = propose_intents(elements)
        if not proposals:
            continue
        text = registry_text(proposals)
        registry = parse_registry_text(text, f"{case.name}.yaml")
        assert registry.issues == (), (case.name, registry.issues)
        assert len(registry.intents) == len(proposals), case.name
        assert all(
            intent.status.value == "PROPOSED" for intent in registry.intents
        ), case.name
        checked += 1
    assert checked >= 20, f"only {checked} cases produced proposals"


def test_a_proposed_intent_is_never_promoted_by_the_round_trip(
    tmp_path: Path,
) -> None:
    """Confirm the loop end to end: propose, feed the file straight back in,
    and no verdict may be ALIGNED or MISALIGNED."""
    target, _specs = _copy_case(WIRE_INTENTS, tmp_path / "t")
    proposed = tmp_path / "proposed.yaml"
    cli.analyze(
        target,
        tmp_path / "g1",
        strict_gate=False,
        propose_intents_path=proposed,
    )
    _code, summary = cli.analyze(
        target,
        tmp_path / "g2",
        strict_gate=False,
        sink_ids=("wire_intents.engine::decide",),
        intents_path=proposed,
    )
    verdicts = summary["intents"]["verdicts"]
    assert verdicts.get(Verdict.ALIGNED.value, 0) == 0
    assert verdicts.get(Verdict.MISALIGNED.value, 0) == 0
    assert verdicts.get(Verdict.UNVERIFIABLE.value, 0) > 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_two_analyses_with_intents_are_byte_identical_across_hash_seeds(
    tmp_path: Path,
) -> None:
    target, specs = _copy_case(WIRE_INTENTS, tmp_path / "t")
    spec = specs / "intents.yaml"
    digests = []
    for index, seed in enumerate(("0", "12345")):
        out = tmp_path / f"run{index}"
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "cascade_map.cli",
                "analyze",
                str(target),
                "--out",
                str(out),
                "--no-gate",
                "--no-progress",
                "--sink",
                "wire_intents.engine::decide",
                "--intents",
                str(spec),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr
        assert _rows(out / "verdicts.jsonl"), "an empty artifact proves nothing"
        digests.append(
            {
                name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                for name in ("intents.jsonl", "verdicts.jsonl", "unresolved.jsonl")
            }
        )
    assert digests[0] == digests[1]


# ---------------------------------------------------------------------------
# Mode A: runtime verdicts
# ---------------------------------------------------------------------------


def _scenario_file(
    tmp_path: Path,
    target_root: Path,
    scenarios: dict[str, Any],
    *,
    client_stubs: dict[str, Any] | None = None,
    name: str = "scenarios.json",
) -> Path:
    document: dict[str, Any] = {
        "target_root": str(target_root),
        "scenarios": scenarios,
        "declared_process_names": [],
        "env_passthrough": [],
    }
    if client_stubs is not None:
        document["client_stubs"] = client_stubs
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_trace_writes_runtime_verdicts_keyed_to_the_run(tmp_path: Path) -> None:
    target, graph, _summary = _analyse(tmp_path)
    spec = tmp_path / "t" / "specs-wire_intents" / "intents.yaml"
    scenarios = _scenario_file(
        tmp_path,
        target,
        {"baseline": {"module": "engine", "function": "main"}},
    )
    code, message = cli.trace(
        graph, scenarios, "baseline", graph, intents_path=spec
    )
    assert code == cli.EXIT_OK, message

    run_dirs = sorted((graph / "runtime").iterdir())
    assert len(run_dirs) == 1
    verdicts = _verdicts_by_element(run_dirs[0] / "verdicts.jsonl")
    assert verdicts, "runtime/<run_id>/verdicts.jsonl must not be empty"
    run_id = run_dirs[0].name
    for row in verdicts.values():
        assert row["provenance"].get("run_id", run_id) in (run_id, "")

    # ALIGNED, from observation: the value expectation the static map could
    # not settle is settled here.
    score = verdicts["wire_intents.engine::score"]
    assert score["verdict"] == Verdict.ALIGNED.value, score["observation"]
    assert score["provenance"]["method"] == Method.RUNTIME_OBSERVED.value
    assert score["provenance"]["event_ids"], "an observed verdict names its events"

    # MISALIGNED, naming the expectation and the contradicting observation.
    gate = verdicts["wire_intents.engine::gate"]
    assert gate["verdict"] == Verdict.MISALIGNED.value
    assert "returns.value >= 0" in gate["expectation"]
    assert gate["evidence_ids"]

    # NOT_EXERCISED, and it is not ALIGNED.
    never = verdicts["wire_intents.engine::never_run"]
    assert never["verdict"] == Verdict.NOT_EXERCISED.value
    assert never["evidence_ids"] == []

    # UNVERIFIABLE, because the intent is PROPOSED.
    assert verdicts["wire_intents.engine::decide"]["verdict"] == Verdict.UNVERIFIABLE.value

    # NO_INTENT, for an element the run entered that no intent names. Reported,
    # not assumed, and neither a pass nor a failure.
    no_intent = verdicts["wire_intents.engine::undeclared_helper"]
    assert no_intent["verdict"] == Verdict.NO_INTENT.value
    assert no_intent["intent_id"] == ""

    # A contradiction the static map already holds outranks "it did not run".
    unplugged = verdicts["wire_intents.engine::declared_live_strategy"]
    assert unplugged["verdict"] == Verdict.MISALIGNED.value
    assert "not exercised" in unplugged["provenance"]["note"]


def test_the_run_summary_says_not_exercised_is_not_aligned(tmp_path: Path) -> None:
    target, graph, _summary = _analyse(tmp_path)
    spec = tmp_path / "t" / "specs-wire_intents" / "intents.yaml"
    scenarios = _scenario_file(
        tmp_path,
        target,
        {"baseline": {"module": "engine", "function": "main"}},
    )
    code, message = cli.trace(
        graph, scenarios, "baseline", graph, intents_path=spec
    )
    assert code == cli.EXIT_OK, message
    assert "NOT_EXERCISED is not ALIGNED" in message
    assert "wire_intents.engine::never_run" in message
    assert "MISALIGNED  wire_intents.engine::gate" in message
    assert "coverage:" in message


def test_a_trace_with_no_intents_says_nothing_was_judged(tmp_path: Path) -> None:
    target, graph, _summary = _analyse(tmp_path, intents="")
    scenarios = _scenario_file(
        tmp_path,
        target,
        {"baseline": {"module": "engine", "function": "main"}},
    )
    code, message = cli.trace(graph, scenarios, "baseline", graph)
    assert code == cli.EXIT_OK, message
    assert "no intents file, so nothing was judged" in message
    run_dirs = sorted((graph / "runtime").iterdir())
    rows = _rows(run_dirs[0] / "verdicts.jsonl")
    assert rows, "every element the run entered is still accounted for"
    assert {row["verdict"] for row in rows} == {Verdict.NO_INTENT.value}, (
        "with no spec every element is NO_INTENT -- a reported state, never a "
        "pass and never a failure"
    )


def test_replaying_the_same_run_twice_gives_byte_identical_verdicts(
    tmp_path: Path,
) -> None:
    """Constraint 4, over the artifact this card adds to a Mode A run."""
    target, graph, _summary = _analyse(tmp_path)
    spec = tmp_path / "t" / "specs-wire_intents" / "intents.yaml"
    scenarios = _scenario_file(
        tmp_path,
        target,
        {"baseline": {"module": "engine", "function": "main"}},
    )
    digests = []
    for index in ("a", "b"):
        out = tmp_path / f"out-{index}"
        code, message = cli.trace(graph, scenarios, "baseline", out, intents_path=spec)
        assert code == cli.EXIT_OK, message
        run_dir = sorted((out / "runtime").iterdir())[0]
        assert _rows(run_dir / "verdicts.jsonl"), (
            "two empty files are byte-identical and prove nothing"
        )
        digests.append(
            (
                run_dir.name,
                hashlib.sha256((run_dir / "verdicts.jsonl").read_bytes()).hexdigest(),
                hashlib.sha256((run_dir / "coverage.json").read_bytes()).hexdigest(),
            )
        )
    assert digests[0] == digests[1]


def test_runtime_verdicts_are_byte_identical_across_hash_seeds(
    tmp_path: Path,
) -> None:
    """Two processes, two hash seeds, one answer.

    In-process replay shares the interpreter's own dict ordering and its
    object addresses, which is precisely how card 12's determinism bug
    survived its own test. Separate processes with different
    `PYTHONHASHSEED`s is the check that can actually fail.
    """
    target, specs = _copy_case(WIRE_INTENTS, tmp_path / "t")
    graph = tmp_path / "graph"
    spec = specs / "intents.yaml"
    env_base = dict(os.environ)
    env_base["PYTHONPATH"] = str(REPO_ROOT / "src")
    assert (
        subprocess.run(
            [
                sys.executable, "-m", "cascade_map.cli", "analyze", str(target),
                "--out", str(graph), "--no-gate", "--no-progress",
                "--sink", "wire_intents.engine::decide", "--intents", str(spec),
            ],
            capture_output=True, text=True, env=env_base,
        ).returncode
        == 0
    )
    scenarios = _scenario_file(
        tmp_path, target, {"baseline": {"module": "engine", "function": "main"}}
    )
    digests = []
    for index, seed in enumerate(("0", "98765")):
        out = tmp_path / f"tr{index}"
        env = dict(env_base)
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [
                sys.executable, "-m", "cascade_map.cli", "trace", str(graph),
                "--scenarios", str(scenarios), "--scenario", "baseline",
                "--out", str(out), "--no-progress", "--intents", str(spec),
            ],
            capture_output=True, text=True, env=env,
        )
        assert completed.returncode == 0, completed.stderr
        run_dir = sorted((out / "runtime").iterdir())[0]
        assert _rows(run_dir / "verdicts.jsonl"), "an empty artifact proves nothing"
        digests.append(
            (
                run_dir.name,
                hashlib.sha256((run_dir / "verdicts.jsonl").read_bytes()).hexdigest(),
                hashlib.sha256((run_dir / "coverage.json").read_bytes()).hexdigest(),
                hashlib.sha256((out / "intents.jsonl").read_bytes()).hexdigest(),
            )
        )
    assert digests[0] == digests[1]


# ---------------------------------------------------------------------------
# The viewer shows the verdict next to the intent it judged
# ---------------------------------------------------------------------------


def test_the_blueprint_carries_the_intent_text_next_to_the_verdict(
    tmp_path: Path,
) -> None:
    from cascade_map.viewer.blueprint import BlueprintView, render_blueprint
    from cascade_map.viewer.loader import ArtifactStore, RuntimeStore

    target, graph, _summary = _analyse(tmp_path)
    spec = tmp_path / "t" / "specs-wire_intents" / "intents.yaml"
    scenarios = _scenario_file(
        tmp_path,
        target,
        {"baseline": {"module": "engine", "function": "main"}},
    )
    code, message = cli.trace(
        graph, scenarios, "baseline", graph, intents_path=spec
    )
    assert code == cli.EXIT_OK, message
    run_id = sorted((graph / "runtime").iterdir())[0].name
    store = ArtifactStore.load(graph)
    rstore = RuntimeStore.load(graph, run_id)
    html = render_blueprint(
        store, rstore, view=BlueprintView(scope="full"), compress=False
    )
    island = _island(html)

    # The statement itself is in the page's data, not only the intent id: a
    # verdict the reader has to go and look an id up for is one they will not
    # read.
    gate = island["element_details"]["wire_intents.engine::gate"]
    assert gate["intents"][0]["statement"] == (
        "Clamps at zero and never returns a negative number."
    )
    assert gate["intents"][0]["status"] == "CONFIRMED"
    assert island["intents_by_id"]["@intent:wire_intents.engine::gate"]["statement"]

    # And the verdict is on the node the page draws, not only in a panel.
    node = next(
        n
        for n in island["execution"]["nodes"]
        if n["id"] == "wire_intents.engine::declared_live_strategy"
    )
    assert node["static_verdict"] == Verdict.MISALIGNED.value
    assert node["intent_count"] == 1

    # The runtime verdict is reachable from the same element.
    runtime = island["runtime"]["verdicts_by_element"][
        "wire_intents.engine::never_run"
    ]
    assert runtime[0]["verdict"] == Verdict.NOT_EXERCISED.value

    # The renderer that puts the two next to each other, and the sentence it
    # prints so NOT_EXERCISED cannot be read as a pass.
    assert "renderIntentSection" in html
    assert "NOT_EXERCISED is not ALIGNED" in html


def test_the_viewer_store_indexes_static_verdicts(tmp_path: Path) -> None:
    from cascade_map.viewer.loader import ArtifactStore
    from cascade_map.viewer import views

    _target, graph, _summary = _analyse(tmp_path)
    store = ArtifactStore.load(graph)
    assert store.available["static_verdicts"] is True
    detail = views.element_detail(store, "wire_intents.engine::declared_live_strategy")
    assert detail["intents"], "the element's own intent must resolve here"
    assert detail["static_verdicts"][0]["verdict"] == Verdict.MISALIGNED.value


def test_the_tabular_view_renders_the_statement_with_the_verdict(
    tmp_path: Path,
) -> None:
    from cascade_map.viewer import render_to_file

    _target, graph, _summary = _analyse(tmp_path)
    out = tmp_path / "page.html"
    render_to_file(graph, out)
    html = out.read_text(encoding="utf-8")
    assert "A live strategy: its output must reach the final decision." in html
    assert "owner-confirmed" in html
    assert "NOT owner-confirmed" in html


# ---------------------------------------------------------------------------
# `reaches decision`, the invariant that answers the owner's question
# ---------------------------------------------------------------------------


def _reach(element_id: str, state: ReachabilityState, reason: str = "") -> Reachability:
    return Reachability(
        id=f"reach:{element_id}",
        element_id=element_id,
        state=state,
        provenance=Provenance(
            method=Method.STRUCTURAL_MATCH, confidence=Confidence.RESOLVED
        ),
        sink_ids=("pkg::decide",),
        reason=reason,
    )


def _intent(element_id: str, invariant: str):
    from cascade_map.contracts.interfaces import Intent, IntentStatus

    return Intent(
        id=f"@intent:{element_id}",
        element_id=element_id,
        status=IntentStatus.CONFIRMED,
        statement="declared",
        invariants=(invariant,),
    )


def test_unknown_reachability_is_never_read_as_unreachable() -> None:
    """Card 3 biases toward REACHES_SINK because a false 'unreachable' sends
    an owner to delete live code. This card must not undo that by reading
    'I could not tell' as 'it does not'."""
    engine = AlignmentEngine(
        reachability=[
            _reach("pkg::x", ReachabilityState.UNKNOWN, "an unresolved call site")
        ],
    )
    verdicts = {
        v.element_id: v
        for v in engine.judge_static([_intent("pkg::x", "reaches decision")])
    }
    verdict = verdicts["pkg::x"]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "never read as" in verdict.observation
    assert "an unresolved call site" in verdict.observation


def test_a_declared_dead_element_that_reaches_the_decision_is_misaligned() -> None:
    """The other direction: the owner said this is off the decision path."""
    engine = AlignmentEngine(
        reachability=[_reach("pkg::x", ReachabilityState.REACHES_SINK)],
    )
    verdicts = {
        v.element_id: v
        for v in engine.judge_static(
            [_intent("pkg::x", "does not reach the decision")]
        )
    }
    assert verdicts["pkg::x"].verdict is Verdict.MISALIGNED


def test_no_reachability_record_is_unverifiable_not_a_contradiction() -> None:
    engine = AlignmentEngine()
    verdicts = {
        v.element_id: v
        for v in engine.judge_static([_intent("pkg::x", "reaches decision")])
    }
    assert verdicts["pkg::x"].verdict is Verdict.UNVERIFIABLE
    assert "never measured" in verdicts["pkg::x"].observation


# ---------------------------------------------------------------------------
# Card 11: client stub declarations
# ---------------------------------------------------------------------------


def test_every_stub_kind_is_named_in_the_error_for_an_unknown_one() -> None:
    assert STUB_KINDS == ("blocked", "record", "replay")
    with pytest.raises(StubDeclarationError) as error:
        parse_declarations({"broker": {"kind": "replya"}})
    for kind in STUB_KINDS:
        assert kind in str(error.value)


@pytest.mark.parametrize(
    "declaration, fragment",
    [
        ({"broker": {"kind": "record"}}, "non-empty"),
        ({"broker": {"kind": "record", "returns": {}}}, "non-empty"),
        ({"broker": {"kind": "replay"}}, "recording"),
        ({"broker": {"kind": "blocked", "returns": {"*": None}}}, "does not take"),
        ({"broker": {"kind": "blocked", "recording": "x.json"}}, "does not take"),
        ({"broker": {"kind": "blocked", "nonsense": 1}}, "unknown key"),
        ({"broker": ["blocked"]}, "must be declared as a mapping"),
        ({"broker": {"kind": "record", "returns": {"*": {1, 2}}}}, "JSON data"),
    ],
)
def test_a_malformed_stub_declaration_is_refused_not_repaired(
    declaration: dict[str, Any], fragment: str
) -> None:
    """Never a fallback to 'blocked'. An owner who read 'declared' in the
    preflight and got a run that stopped at the first call would have no way
    to find out why."""
    with pytest.raises(StubDeclarationError) as error:
        parse_declarations(declaration)
    assert fragment in str(error.value)


def test_the_stub_kind_reaches_the_run_id() -> None:
    """Two runs that stub the same module two different ways are two
    different runs, and replay compares output keyed by run id."""
    blocked = declaration_fingerprint(parse_declarations({"b": {"kind": "blocked"}}))
    recorded = declaration_fingerprint(
        parse_declarations({"b": {"kind": "record", "returns": {"*": None}}})
    )
    assert blocked and recorded and blocked != recorded
    assert declaration_fingerprint(()) == "", (
        "a run with no declared stub must keep the run id it had before stub "
        "kinds existed"
    )


def _stub_graph(tmp_path: Path) -> tuple[Path, Path, Path]:
    target, specs = _copy_case(WIRE_STUBS, tmp_path / "t")
    graph = tmp_path / "graph"
    code, summary = cli.analyze(target, graph, strict_gate=False)
    assert code in (cli.EXIT_OK, cli.EXIT_GATE_FAILED), summary
    return target, specs, graph


def _trace_stub(
    tmp_path: Path,
    function: str,
    stubs: dict[str, Any],
    *,
    out_name: str = "out",
) -> tuple[int, str, Path]:
    target, specs, graph = _stub_graph(tmp_path)
    for body in stubs.values():
        if body.get("recording"):
            body["recording"] = str((specs / body["recording"]).resolve())
    scenarios = _scenario_file(
        tmp_path,
        target,
        {"s": {"module": "clients", "function": function}},
        client_stubs=stubs,
    )
    out = tmp_path / out_name
    code, message = cli.trace(graph, scenarios, "s", out)
    return code, message, out


def test_a_blocked_stub_stops_the_call_and_records_it(tmp_path: Path) -> None:
    """Today's behaviour, now STATED rather than implicit -- and recorded at
    the client boundary, naming the module and the call."""
    code, message, out = _trace_stub(
        tmp_path, "use_blocked", {"wire_stub_blocked": {"kind": "blocked"}}
    )
    assert code == cli.EXIT_OK, message
    run = json.loads(
        (sorted((out / "runtime").iterdir())[0] / "run.json").read_text(
            encoding="utf-8"
        )
    )
    stopped = [item for item in run["blocked"] if item["kind"] == "client_stub"]
    assert stopped, run["blocked"]
    assert "wire_stub_blocked.submit_order" in stopped[0]["detail"]
    assert "by your own declaration" in stopped[0]["detail"]
    assert "wire_stub_blocked [blocked]" in message


def test_a_record_stub_captures_the_call_and_returns_the_declared_default(
    tmp_path: Path,
) -> None:
    code, message, out = _trace_stub(
        tmp_path,
        "use_record",
        {
            "wire_stub_record": {
                "kind": "record",
                "returns": {"*": None, "ping": True, "fetch_rows": [1, 2]},
            }
        },
    )
    assert code == cli.EXIT_OK, message
    run_dir = sorted((out / "runtime").iterdir())[0]
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert [i for i in run["blocked"] if i["kind"] == "client_stub"] == [], run[
        "blocked"
    ]
    assert run["scenario_failure"] is None, run["scenario_failure"]
    calls = _rows(out / "sandbox" / "client_calls" / "wire_stub_record.jsonl")
    assert [row["path"] for row in calls] == ["ping", "fetch_rows"]
    assert calls[1]["args"] == ["'select 1'"]
    assert all(row["outcome"] == "recorded" for row in calls)


def test_a_record_stub_refuses_a_call_it_has_no_declared_return_for(
    tmp_path: Path,
) -> None:
    """A quiet None here is a wrong answer wearing the shape of a right one."""
    code, message, out = _trace_stub(
        tmp_path,
        "use_record_undeclared_path",
        {"wire_stub_record": {"kind": "record", "returns": {"ping": True}}},
    )
    assert code == cli.EXIT_OK, message
    run = json.loads(
        (sorted((out / "runtime").iterdir())[0] / "run.json").read_text(
            encoding="utf-8"
        )
    )
    stopped = [item for item in run["blocked"] if item["kind"] == "client_stub"]
    assert stopped, run["blocked"]
    assert "something_nobody_declared" in stopped[0]["detail"]
    assert "will not invent a return value" in stopped[0]["detail"]


def test_a_replay_stub_returns_the_owners_recorded_values_in_order(
    tmp_path: Path,
) -> None:
    code, message, out = _trace_stub(
        tmp_path,
        "use_replay",
        {"wire_stub_replay": {"kind": "replay", "recording": "replay_feed.json"}},
    )
    assert code == cli.EXIT_OK, message
    run_dir = sorted((out / "runtime").iterdir())[0]
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert [i for i in run["blocked"] if i["kind"] == "client_stub"] == []
    assert run["scenario_failure"] is None
    events = _rows(run_dir / "events.jsonl")
    returns = [
        event
        for event in events
        if event["kind"] == "RETURN" and event["element_id"].endswith("use_replay")
    ]
    assert returns, "the scenario's return must have been observed"
    assert "101" in json.dumps(returns[0]["values"])
    assert "102" in json.dumps(returns[0]["values"])


def test_a_replay_past_the_end_of_the_recording_is_a_hard_stop(
    tmp_path: Path,
) -> None:
    code, message, out = _trace_stub(
        tmp_path,
        "use_replay_past_the_end",
        {"wire_stub_replay": {"kind": "replay", "recording": "replay_feed.json"}},
    )
    assert code == cli.EXIT_OK, message
    run = json.loads(
        (sorted((out / "runtime").iterdir())[0] / "run.json").read_text(
            encoding="utf-8"
        )
    )
    stopped = [item for item in run["blocked"] if item["kind"] == "client_stub"]
    assert stopped, run["blocked"]
    assert "never loops and never invents a value" in stopped[0]["detail"]


def test_a_missing_replay_recording_refuses_before_anything_runs(
    tmp_path: Path,
) -> None:
    _target, _specs, graph = _stub_graph(tmp_path)
    scenarios = _scenario_file(
        tmp_path,
        cli.target_root_of(graph),
        {"s": {"module": "clients", "function": "use_replay"}},
        client_stubs={
            "wire_stub_replay": {"kind": "replay", "recording": "nowhere.json"}
        },
    )
    out = tmp_path / "out"
    code, message = cli.trace(graph, scenarios, "s", out)
    assert code == cli.EXIT_REFUSED
    assert "does not exist" in message
    assert "Nothing was executed" in message
    assert not (out / "runtime").exists()


def test_a_malformed_stub_declaration_refuses_the_run(tmp_path: Path) -> None:
    _target, _specs, graph = _stub_graph(tmp_path)
    scenarios = _scenario_file(
        tmp_path,
        cli.target_root_of(graph),
        {"s": {"module": "clients", "function": "use_blocked"}},
        client_stubs={"wire_stub_blocked": {"kind": "pretend"}},
    )
    out = tmp_path / "out"
    code, message = cli.trace(graph, scenarios, "s", out)
    assert code == cli.EXIT_REFUSED
    assert "Nothing was executed" in message
    assert not (out / "runtime").exists()


def test_declaring_one_stub_is_never_a_blanket_exemption(tmp_path: Path) -> None:
    """The hard rule, unchanged: an UNDECLARED client is still a hard stop.

    `wire_stub_blocked` is declared. `wire_stub_nobody_declared` is not, and
    the run must not reach it -- declaring one module is taking
    responsibility for one module.
    """
    code, message, out = _trace_stub(
        tmp_path, "use_undeclared", {"wire_stub_blocked": {"kind": "blocked"}}
    )
    assert code == cli.EXIT_OK, message
    run = json.loads(
        (sorted((out / "runtime").iterdir())[0] / "run.json").read_text(
            encoding="utf-8"
        )
    )
    failure = run["scenario_failure"]
    assert failure is not None, "the undeclared module must not have imported"
    assert "wire_stub_nobody_declared" in failure["message"]


def test_the_preflight_names_every_declared_stub_and_its_kind(
    tmp_path: Path,
) -> None:
    _target, _specs, graph = _stub_graph(tmp_path)
    scenarios = _scenario_file(
        tmp_path,
        cli.target_root_of(graph),
        {"s": {"module": "clients", "function": "use_blocked"}},
        client_stubs={
            "wire_stub_blocked": {"kind": "blocked"},
            "wire_stub_record": {"kind": "record", "returns": {"*": None}},
        },
    )
    from cascade_map.ledger import Settings

    code, message = cli.preflight(
        graph,
        tmp_path / "out",
        Settings.from_mapping({}),
        (),
        scenarios,
    )
    assert "wire_stub_blocked [blocked]" in message
    assert "wire_stub_record [record]" in message
    assert "NOTHING ELSE is exempt" in message
    assert code == cli.EXIT_OK, message


def test_the_preflight_refuses_a_stub_block_it_cannot_read(tmp_path: Path) -> None:
    _target, _specs, graph = _stub_graph(tmp_path)
    scenarios = _scenario_file(
        tmp_path,
        cli.target_root_of(graph),
        {"s": {"module": "clients", "function": "use_blocked"}},
        client_stubs={"wire_stub_blocked": {"kind": "nope"}},
    )
    code, message = cli.preflight(
        graph, tmp_path / "out", cli.Settings.from_mapping({}), (), scenarios
    )
    assert code == cli.EXIT_REFUSED
    assert "WOULD REFUSE TO START" in message


def test_two_stubbed_runs_produce_byte_identical_output(tmp_path: Path) -> None:
    """Constraint 4 over a run whose external systems are stubbed. The call
    log carries argument reprs, so a memory address leaking into one would
    break this -- which is exactly how card 12's determinism bug was found."""
    digests = []
    for index in ("a", "b"):
        code, message, out = _trace_stub(
            tmp_path / index,
            "use_record",
            {
                "wire_stub_record": {
                    "kind": "record",
                    "returns": {"*": None, "ping": True},
                }
            },
        )
        assert code == cli.EXIT_OK, message
        calls = out / "sandbox" / "client_calls" / "wire_stub_record.jsonl"
        run_dir = sorted((out / "runtime").iterdir())[0]
        digests.append(
            (
                run_dir.name,
                hashlib.sha256(calls.read_bytes()).hexdigest(),
                hashlib.sha256((run_dir / "events.jsonl").read_bytes()).hexdigest(),
            )
        )
    assert digests[0] == digests[1]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_intents_is_a_setting_in_both_dicts_and_is_a_string_path() -> None:
    from cascade_map.ledger import SETTING_DEFAULTS, Settings, SettingsError

    assert "INTENTS" in cli.METATRON_SETTINGS
    assert "INTENTS" in SETTING_DEFAULTS
    assert set(cli.METATRON_SETTINGS) == set(SETTING_DEFAULTS)
    assert Settings.from_mapping({"INTENTS": "a.yaml"}).intents == "a.yaml"
    with pytest.raises(SettingsError):
        Settings.from_mapping({"INTENTS": ["a.yaml"]})


def test_the_intents_flag_overrides_the_setting(tmp_path: Path) -> None:
    class Args:
        intents = tmp_path / "flag.yaml"

    merged = cli._settings_from_args(Args())
    assert merged.intents == str(tmp_path / "flag.yaml")


# ---------------------------------------------------------------------------
# The refusal: a stub that cannot be built must never fall through to the
# real module
# ---------------------------------------------------------------------------


_REFUSAL_SCRIPT = """
import json, sys
sys.path.insert(0, {src!r})
from pathlib import Path
from cascade_map.harness import Harness, RunConfig, ScenarioSpec
from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes

target_root = Path({target!r})
out_dir = Path({out!r})


def exploding_factory():
    raise RuntimeError("this stub cannot be built")


config = RunConfig(
    target_root=target_root,
    mode_b_out_dir=out_dir,
    sandbox_root=Path({sandbox!r}),
    scenarios={{"s": ScenarioSpec(name="s", module="clients", function="use_blocked")}},
    client_stubs={{"wire_stub_blocked": exploding_factory}},
    client_declarations='{{"wire_stub_blocked":{{"kind":"blocked"}}}}',
)
graph_hash = compute_graph_hash(compute_target_hashes(target_root))
record = Harness(config).start("s", graph_hash)
print(json.dumps({{"refused": record.refused, "reason": record.refusal_reason}}))
"""


def test_the_harness_refuses_when_a_declared_stub_cannot_be_built(
    tmp_path: Path,
) -> None:
    """The guarantee that makes a declaration mean anything.

    If a stub cannot be constructed, the alternative to refusing is letting
    the scenario import the REAL module -- the owner reads "declared" in the
    preflight and the run reaches a live broker. `_execute` catches setup
    failures and discards them by design, so this path has to raise a refusal
    to survive that handler; this test is what proves it does.

    Run in a separate interpreter: the sandbox's enforcement flag never
    clears, so an in-process run would sandbox the rest of the session.
    """
    target, _specs = _copy_case(WIRE_STUBS, tmp_path / "t")
    out_dir = tmp_path / "graph"
    out_dir.mkdir(parents=True)
    (out_dir / "elements.jsonl").write_text("", encoding="utf-8")
    script = _REFUSAL_SCRIPT.format(
        src=str(REPO_ROOT / "src"),
        target=str(target),
        out=str(out_dir),
        sandbox=str(tmp_path / "sandbox"),
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["refused"] is True, "the run must not proceed without the stub"
    assert "could not be built" in result["reason"]
    assert "wire_stub_blocked" in result["reason"]
    assert "real" in result["reason"]
    # And the refusal is a first-class record on disk, like every other one.
    records = list((out_dir / "runtime").glob("*/run.json"))
    assert len(records) == 1
    written = json.loads(records[0].read_text(encoding="utf-8"))
    assert written["refused"] is True


# ---------------------------------------------------------------------------
# The empty-return probe, on this module
# ---------------------------------------------------------------------------
#
# Stub the two entry points this wiring exists to reach -- the registry loader
# and the verdict engine -- and run this file again. Whatever still passes was
# never testing the wiring. The surviving count is printed, not hidden.

import re  # noqa: E402
import textwrap  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_STUB = """
import sys
sys.path.insert(0, {src!r})

from cascade_map import alignment
from cascade_map.alignment import IntentRegistry


def _empty_registry(path=None, known_element_ids=None):
    # The worst plausible card 13: it reads nothing, reports nothing wrong,
    # and every element comes out NO_INTENT. That is exactly the "an intents
    # file that silently did nothing" outcome this wiring exists to forbid.
    return IntentRegistry(source_path=str(path or ""), present=False)


class _Silent(alignment.AlignmentEngine):
    def judge(self, intents, events):
        return ()

    def judge_static(self, intents):
        return ()


alignment.load_registry = _empty_registry
alignment.AlignmentEngine = _Silent

from cascade_map import cli  # noqa: E402

cli.load_registry = _empty_registry
cli.AlignmentEngine = _Silent
"""


def test_the_suite_does_not_pass_against_a_silent_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if os.environ.get("CASCADE_MAP_INTENT_PROBE"):
        pytest.skip("already inside the empty-return probe")
    plugin = tmp_path / "intent_stub_plugin.py"
    plugin.write_text(
        textwrap.dedent(_STUB.format(src=str(REPO / "src"))), encoding="utf-8"
    )
    env = dict(
        os.environ,
        PYTHONPATH=f"{tmp_path}{os.pathsep}{REPO / 'src'}",
        CASCADE_MAP_INTENT_PROBE="1",
    )
    report = tmp_path / "probe.xml"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__).resolve()),
            "-p",
            "intent_stub_plugin",
            "-p",
            "no:randomly",
            "--tb=no",
            "-q",
            f"--junitxml={report}",
            "-k",
            "not silent_registry",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        timeout=900,
    )
    # Counted from the machine-readable report, not scraped off a terminal
    # line: a probe that reports "0 survived" because it failed to parse its
    # own output is the same class of false clean result this card is about.
    assert report.is_file(), "the probe run produced no report"
    import xml.etree.ElementTree as ElementTree

    suite = ElementTree.parse(report).getroot()[0].attrib
    total = int(suite["tests"])
    bad = int(suite["failures"]) + int(suite["errors"])
    passed = total - bad - int(suite.get("skipped", 0))
    assert total > 40, f"the probe only ran {total} tests"
    summary = f"{total} ran, {bad} failed"
    with capsys.disabled():
        print(
            f"\nempty-return probe: {passed} of this file's tests pass against "
            f"a registry that reads nothing and an engine that judges nothing "
            f"({summary})"
        )
    # What may legitimately survive: the client-stub half of this module,
    # which is card 11 and does not touch the registry, plus the settings
    # checks. Everything about intents must fail.
    # What may legitimately survive: the 22 client-stub tests (card 11, which
    # never touches the registry), the two settings checks, the two CLI guards
    # that refuse before card 13 is reached, and the four that exercise
    # `propose_intents`/`parse_registry_text` directly or drive a subprocess
    # the stub plugin is not loaded into.
    assert passed <= 30, (
        f"{passed} tests pass against a card 13 that does nothing; they are "
        f"not testing the wiring"
    )
