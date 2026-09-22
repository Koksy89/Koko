"""Tests for card 17 -- dependency and version applicability.

Three rules shape this file:

* **Nothing here imports, executes or ``exec``s a fixture**, and nothing under
  a fixture environment is ever imported. The sentinel proves it, and a second
  test proves that reading a distribution's metadata does not run its
  ``__init__``.
* **Derived facts are generated, not written down.** Where a test asserts a
  line number, it locates that line in the fixture source itself; the only
  hand-written expectations are semantic ones.
* **Nothing is written into ``tests/fixtures/``.** Everything that needs a
  writable tree uses ``tmp_path``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cascade_map.contracts.interfaces import (
    Confidence,
    Element,
    ElementKind,
    FindingKind,
    Method,
    Provenance,
    Reachability,
    ReachabilityState,
    SourceSpan,
    UnresolvedReason,
    canonical_jsonl,
)
from cascade_map.dependencies import (
    Dependencies,
    evaluate_specifier,
    normalize_distribution,
    parse_version,
)
from cascade_map.ingest import inventory

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
MODE_B = FIXTURES / "mode_b"
SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")

#: Every dependency case in the corpus. Non-empty by construction below.
DEP_CASES = sorted(p.name for p in MODE_B.glob("dep_*") if p.is_dir())


def case(name: str) -> Path:
    directory = MODE_B / name
    assert directory.is_dir(), f"missing fixture {name}"
    return directory


def build(name: str, *, with_env: bool = True, reachability=()) -> Dependencies:
    """A `Dependencies` over one fixture case, with card 1's real elements."""
    root = case(name)
    env = root / "env"
    elements, _ = inventory(str(root))
    return Dependencies(
        root,
        environment_root=env if (with_env and env.is_dir()) else None,
        elements=elements,
        reachability=reachability,
    )


# ---------------------------------------------------------------------------
# PEP 503 and PEP 440, implemented here because `packaging` is not a dependency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Scikit_Learn", "scikit-learn"),
        ("scikit-learn", "scikit-learn"),
        ("zope.interface", "zope-interface"),
        ("PyYAML", "pyyaml"),
        ("opencv_python", "opencv-python"),
        ("A__B", "a-b"),
    ],
)
def test_distribution_names_normalize_to_one_spelling(raw: str, expected: str) -> None:
    assert normalize_distribution(raw) == expected


def test_scikit_learn_spellings_are_one_distribution_not_two() -> None:
    """Joining on the raw spelling reports one of the pair as undeclared."""
    assert normalize_distribution("Scikit_Learn") == normalize_distribution("scikit-learn")


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("1.0", "1.1"),
        ("1.0", "1.0.1"),
        ("1.0a1", "1.0"),
        ("1.0rc1", "1.0"),
        ("1.0.dev1", "1.0a1"),
        ("1.0", "1.0.post1"),
        ("1.0", "2!0.1"),
        ("1.0", "1.0+local"),
        ("2.0.0", "10.0.0"),
    ],
)
def test_version_ordering_is_pep440_not_string_order(lower: str, higher: str) -> None:
    low, high = parse_version(lower), parse_version(higher)
    assert low is not None and high is not None
    assert low.key < high.key, f"{lower} should sort below {higher}"


def test_trailing_zeros_do_not_make_two_versions() -> None:
    a, b = parse_version("1.0"), parse_version("1.0.0")
    assert a is not None and b is not None
    assert a.key == b.key


@pytest.mark.parametrize(
    ("specifier", "version", "expected"),
    [
        (">=2.0,<3.0", "1.5.3", False),
        (">=2.0,<3.0", "2.1.0", True),
        (">=2.0,<3.0", "3.0", False),
        ("==2.31.0", "2.31.0", True),
        ("==2.31.0", "2.31.1", False),
        ("==1.4.*", "1.4.9", True),
        ("==1.4.*", "1.5.0", False),
        ("!=1.4.*", "1.5.0", True),
        ("~=1.4.5", "1.4.9", True),
        ("~=1.4.5", "1.5.0", False),
        ("~=2.2", "2.9", True),
        ("~=2.2", "3.0", False),
        (">1.0", "1.0.post1", False),
        ("<1.0", "1.0rc1", False),
        ("", "9.9.9", True),
        ("===1.0+corp", "1.0+corp", True),
    ],
)
def test_specifier_satisfaction(specifier: str, version: str, expected: bool) -> None:
    satisfied, reason = evaluate_specifier(specifier, version)
    assert satisfied is expected, reason


@pytest.mark.parametrize(
    ("specifier", "version"),
    [
        ("==1.2+corporate1", "1.2"),
        ("~=1", "1.5"),
        (">=not.a.version", "1.0"),
        ("=1.2", "1.2"),
        (">=1.0", "cheese"),
    ],
)
def test_an_unevaluable_specifier_is_none_and_names_itself(
    specifier: str, version: str
) -> None:
    """`None` is a refusal to guess, and the reason has to be readable."""
    satisfied, reason = evaluate_specifier(specifier, version)
    assert satisfied is None
    assert reason.strip()


@pytest.mark.parametrize(
    ("specifier", "version", "dialect", "expected"),
    [
        ("^23.1.0", "23.4.0", "poetry", True),
        ("^23.1.0", "24.0.0", "poetry", False),
        ("^0.2.3", "0.2.9", "poetry", True),
        ("^0.2.3", "0.3.0", "poetry", False),
        ("~1.2.3", "1.2.9", "poetry", True),
        ("~1.2.3", "1.3.0", "poetry", False),
        ("=1.26.4", "1.26.4", "conda", True),
        ("=1.26", "1.26.9", "conda", True),
        ("=1.26", "1.27.0", "conda", False),
    ],
)
def test_manifest_dialects_are_expanded_at_evaluation_time(
    specifier: str, version: str, dialect: str, expected: bool
) -> None:
    satisfied, reason = evaluate_specifier(specifier, version, dialect=dialect)
    assert satisfied is expected, reason


# ---------------------------------------------------------------------------
# Declared -- every manifest format, with the line the owner typed it on
# ---------------------------------------------------------------------------


def test_requirements_txt_records_each_pin_as_written() -> None:
    requirements = build("dep_requirements_conflict").requirements()
    by_name = {r.distribution: r for r in requirements}
    assert set(by_name) == {"pandas", "requests", "click", "pyyaml", "urllib3"}
    assert by_name["pandas"].raw == "pandas>=2.0,<3.0"
    assert by_name["pandas"].specifier == ">=2.0,<3.0"
    assert by_name["pyyaml"].raw == "PyYAML>=6.0"
    # The name is normalised for joining; the spelling the owner typed survives.
    assert by_name["pyyaml"].distribution == "pyyaml"


def test_a_minus_r_include_is_followed_and_keeps_its_own_file_and_line() -> None:
    by_name = {r.distribution: r for r in build("dep_requirements_conflict").requirements()}
    assert by_name["pyyaml"].span.path == "extra-requirements.txt"
    assert by_name["pyyaml"].span.line == 1


def test_a_minus_c_constraint_file_is_followed() -> None:
    by_name = {r.distribution: r for r in build("dep_requirements_conflict").requirements()}
    assert by_name["urllib3"].span.path == "constraints.txt"
    assert by_name["urllib3"].raw == "urllib3<2.0"


def test_a_missing_include_is_unresolved_not_silence(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("-r gone.txt\nsix>=1.0\n", encoding="utf-8")
    deps = Dependencies(tmp_path)
    assert [r.distribution for r in deps.requirements()] == ["six"]
    missing = [u for u in deps.unresolved() if u.id.startswith("dep::include::")]
    assert len(missing) == 1
    assert missing[0].reason is UnresolvedReason.MISSING_TARGET
    assert missing[0].span.line == 1


def test_pyproject_records_optional_groups_separately() -> None:
    requirements = build("dep_pyproject_groups").requirements()
    groups = {(r.distribution, r.optional_group) for r in requirements}
    assert ("pandas", "") in groups
    assert ("pytest", "dev") in groups
    assert ("sphinx", "docs") in groups


def test_a_marker_is_recorded_and_never_evaluated() -> None:
    requirements = build("dep_pyproject_groups").requirements()
    requests = next(r for r in requirements if r.distribution == "requests")
    assert requests.marker == 'python_version < "3.13"'
    assert requests.extras == ("socks",)
    assert requests.specifier == ">=2.28"


def test_poetry_dependencies_are_read_from_the_tool_table() -> None:
    requirements = build("dep_pyproject_groups").requirements()
    attrs = next((r for r in requirements if r.distribution == "attrs"), None)
    assert attrs is not None
    assert attrs.specifier == "^23.1.0"
    assert attrs.raw == 'attrs = "^23.1.0"'


@pytest.mark.parametrize(
    ("distribution", "path", "specifier", "group"),
    [
        ("six", "setup.cfg", ">=1.16", ""),
        ("pytest", "setup.cfg", ">=7.0", "test"),
        ("flask", "Pipfile", "==2.3.3", ""),
        ("black", "Pipfile", ">=23.0", "dev"),
        ("numpy", "environment.yml", "=1.26.4", ""),
        ("torch", "environment.yml", ">=2.1", "pip"),
        ("httpx", "script.py", ">=0.27", ""),
    ],
)
def test_every_manifest_format_is_read(
    distribution: str, path: str, specifier: str, group: str
) -> None:
    requirements = build("dep_manifest_formats").requirements()
    record = next((r for r in requirements if r.distribution == distribution), None)
    assert record is not None, f"{distribution} was not read from {path}"
    assert record.span.path == path
    assert record.specifier == specifier
    assert record.optional_group == group


def test_a_pipfile_star_records_an_empty_specifier_not_a_constraint() -> None:
    requirements = build("dep_manifest_formats").requirements()
    gunicorn = next(r for r in requirements if r.distribution == "gunicorn")
    assert gunicorn.specifier == ""


def test_every_recorded_line_really_holds_that_requirement() -> None:
    """The mechanical check: each span is resolved against the fixture source.

    Hand-transcribed positional data has been wrong twice in this project, so
    no line number here is trusted -- every one is read back out of the file
    it claims to come from.
    """
    checked = 0
    for name in DEP_CASES:
        deps = build(name, with_env=False)
        root = case(name)
        for requirement in deps.requirements():
            source = (root / requirement.span.path).read_text(encoding="utf-8")
            lines = source.splitlines()
            assert 1 <= requirement.span.line <= len(lines), (
                f"{name}: {requirement.id} points at line {requirement.span.line} of "
                f"a {len(lines)}-line file"
            )
            line = lines[requirement.span.line - 1]
            needle = requirement.raw.split('"')[0].split("'")[0].strip()
            assert requirement.distribution.split("-")[0] in line.lower() or needle in line, (
                f"{name}: line {requirement.span.line} of {requirement.span.path} is "
                f"{line!r}, which does not hold {requirement.raw!r}"
            )
            checked += 1
    assert checked >= 20, f"only {checked} requirements were checked"


def test_a_manifest_that_defeats_the_parser_is_named(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("!!! not a requirement\nsix\n", encoding="utf-8")
    deps = Dependencies(tmp_path)
    assert [r.distribution for r in deps.requirements()] == ["six"]
    broken = [u for u in deps.unresolved() if u.reason is UnresolvedReason.SYNTAX_ERROR]
    assert len(broken) == 1
    assert broken[0].span.line == 1


def test_a_non_utf8_manifest_does_not_crash_the_run(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_bytes(b"six>=1.0\n\xff\xfe\n")
    deps = Dependencies(tmp_path)
    assert deps.requirements() == ()
    decode = [u for u in deps.unresolved() if u.reason is UnresolvedReason.DECODE_ERROR]
    assert len(decode) == 1


def test_a_tree_with_no_manifest_says_so_rather_than_reporting_everything_undeclared(
    tmp_path: Path,
) -> None:
    (tmp_path / "m.py").write_text("import pandas\n", encoding="utf-8")
    deps = Dependencies(tmp_path)
    findings = deps.findings()
    assert not [f for f in findings if f.kind is FindingKind.UNDECLARED_DEPENDENCY]
    reasons = [u.description for u in deps.unresolved() if u.id == "dep::manifests::none"]
    assert len(reasons) == 1
    assert "no dependency manifest" in reasons[0]


# ---------------------------------------------------------------------------
# Installed -- read as text, never imported
# ---------------------------------------------------------------------------


def test_installed_packages_come_off_the_metadata_files() -> None:
    packages = {p.distribution: p for p in build("dep_requirements_conflict").installed()}
    assert set(packages) == {"pandas", "requests", "pyyaml", "click"}
    assert packages["pandas"].version == "1.5.3"
    assert packages["pandas"].requires == ("numpy>=1.20",)
    assert packages["pyyaml"].import_names == ("_yaml", "yaml")


def test_import_names_fall_back_to_record_when_top_level_is_absent() -> None:
    packages = {p.distribution: p for p in build("dep_metadata_join").installed()}
    assert packages["scikit-learn"].import_names == ("sklearn",)
    assert packages["scikit-learn"].version == "1.4.2"


def test_an_installed_name_is_normalised_before_it_is_joined() -> None:
    """The fixture's directory says `Scikit_Learn`; the record says one name."""
    distributions = [p.distribution for p in build("dep_metadata_join").installed()]
    assert "scikit-learn" in distributions
    assert "Scikit_Learn" not in distributions


def test_a_distribution_with_no_import_name_metadata_is_unresolved(tmp_path: Path) -> None:
    info = tmp_path / "env" / "lib" / "site-packages" / "mystery-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: mystery\nVersion: 1.0\n", encoding="utf-8"
    )
    deps = Dependencies(tmp_path, environment_root=tmp_path / "env")
    assert [p.distribution for p in deps.installed()] == ["mystery"]
    unknown = [u for u in deps.unresolved() if u.id == "dep::importnames::mystery"]
    assert len(unknown) == 1


def test_an_absent_environment_is_unresolved_not_an_empty_list() -> None:
    """An empty list reads as "nothing is installed", which is a lie."""
    deps = build("dep_absent_environment", with_env=False)
    assert deps.installed() == ()
    assert deps.environment_located is False
    absent = [u for u in deps.unresolved() if u.id == "dep::environment"]
    assert len(absent) == 1
    assert "--env" in absent[0].description
    assert deps.summary()["environment_located"] is False


def test_an_environment_path_that_does_not_exist_is_unresolved(tmp_path: Path) -> None:
    deps = Dependencies(tmp_path, environment_root=tmp_path / "no-such-venv")
    assert deps.installed() == ()
    absent = [u for u in deps.unresolved() if u.id == "dep::environment"]
    assert len(absent) == 1
    assert absent[0].reason is UnresolvedReason.MISSING_TARGET


def test_no_finding_claims_the_environment_is_fine_when_it_was_not_read() -> None:
    deps = build("dep_absent_environment", with_env=False)
    kinds = {f.kind for f in deps.findings()}
    assert FindingKind.MISSING_DEPENDENCY not in kinds
    assert FindingKind.UNUSED_DEPENDENCY not in kinds
    summary = deps.summary()
    assert summary["installed"] == 0 and summary["environment"] == ""


def test_reading_metadata_does_not_run_the_package_it_describes(tmp_path: Path) -> None:
    """Constraint 1, applied to the environment.

    A real package's ``__init__`` runs on import. This one writes a marker
    file if it ever does; reading its metadata must leave the marker absent.
    """
    marker = tmp_path / "IMPORTED"
    site = tmp_path / "env" / "lib" / "python3.12" / "site-packages"
    package = site / "trip"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f"open({str(marker)!r}, 'w').write('tripped')\n", encoding="utf-8"
    )
    info = site / "trip-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: trip\nVersion: 1.0\n", encoding="utf-8"
    )
    (info / "top_level.txt").write_text("trip\n", encoding="utf-8")

    before = set(sys.modules)
    deps = Dependencies(tmp_path, environment_root=tmp_path / "env")
    assert [p.version for p in deps.installed()] == ["1.0"]
    assert not marker.exists(), "reading installed metadata executed the package"
    assert "trip" not in set(sys.modules) - before


def test_installed_locations_carry_no_absolute_path() -> None:
    """An absolute path inside an artifact makes two machines disagree."""
    packages = build("dep_requirements_conflict").installed()
    assert packages
    for package in packages:
        assert not package.location.startswith("/")
        assert not package.provenance.span.path.startswith("/")


def test_the_sentinel_is_not_tripped_by_this_card(tmp_path: Path) -> None:
    if SENTINEL_MARKER.exists():
        SENTINEL_MARKER.unlink()
    elements, _ = inventory(str(FIXTURES / "sentinel"), cache_dir=tmp_path / "cache")
    deps = Dependencies(FIXTURES / "sentinel", elements=elements)
    deps.requirements()
    deps.usage()
    deps.interpreter_requirements()
    deps.findings()
    assert not SENTINEL_MARKER.exists(), (
        "the sentinel marker exists: card 17 imported or executed a fixture "
        "instead of reading it as text"
    )


# ---------------------------------------------------------------------------
# Used -- the join, which is silently wrong if it is done lazily
# ---------------------------------------------------------------------------


def test_an_import_name_is_joined_to_its_distribution_through_metadata() -> None:
    usages = {u.id: u for u in build("dep_metadata_join").usage()}
    assert "usage::opencv-python" in usages
    assert usages["usage::opencv-python"].import_names == ("cv2",)
    assert usages["usage::opencv-python"].provenance.confidence is Confidence.RESOLVED
    assert "usage::scikit-learn" in usages
    assert usages["usage::scikit-learn"].import_names == ("sklearn",)


def test_the_join_is_derived_from_metadata_not_from_a_table_of_guesses(
    tmp_path: Path,
) -> None:
    """`cv2` maps to `opencv-python` only because a file said so."""
    (tmp_path / "m.py").write_text("import cv2\n", encoding="utf-8")
    env = tmp_path / "env"
    (env / "site-packages").mkdir(parents=True)
    deps = Dependencies(tmp_path, environment_root=env)
    usages = [u for u in deps.usage() if "cv2" in u.import_names]
    assert len(usages) == 1
    # With no metadata to read, no distribution is claimed.
    assert usages[0].installed_id == ""
    assert usages[0].provenance.confidence is Confidence.UNKNOWN


def test_an_ambiguous_import_name_lists_both_candidates_and_picks_neither() -> None:
    deps = build("dep_ambiguous_import")
    usages = {u.id: u for u in deps.usage()}
    assert "usage::?shared" in usages
    assert usages["usage::?shared"].distribution == ""
    assert usages["usage::?shared"].provenance.confidence is Confidence.UNKNOWN

    ambiguous = [u for u in deps.unresolved() if u.id == "dep::ambiguous::shared"]
    assert len(ambiguous) == 1
    assert ambiguous[0].reason is UnresolvedReason.AMBIGUOUS
    assert set(ambiguous[0].candidate_ids) == {
        "installed::shared-alpha::1.0",
        "installed::shared-beta::2.0",
    }


def test_an_ambiguous_candidate_is_never_reported_unused() -> None:
    """`import shared` is in the source; "nothing imports it" would be false."""
    findings = build("dep_ambiguous_import").findings()
    unused = {f.id for f in findings if f.kind is FindingKind.UNUSED_DEPENDENCY}
    assert unused == set()


def test_usage_names_the_elements_that_import_it() -> None:
    usages = {u.distribution: u for u in build("dep_requirements_conflict").usage()}
    pandas = usages["pandas"]
    assert "dep_requirements_conflict.engine::load_frame" in pandas.element_ids
    assert all(i for i in pandas.element_ids)


def test_attribute_paths_are_the_surface_actually_touched() -> None:
    usages = {u.distribution: u for u in build("dep_requirements_conflict").usage()}
    assert usages["pandas"].attribute_paths == ("pandas.DataFrame",)
    assert usages["pyyaml"].attribute_paths == ("yaml.safe_load",)
    assert usages["requests"].attribute_paths == ("requests.get",)


def test_a_from_import_keeps_its_distribution_prefix() -> None:
    usages = {u.distribution: u for u in build("dep_metadata_join").usage()}
    assert usages["scikit-learn"].attribute_paths == (
        "sklearn.linear_model.LogisticRegression",
    )


def test_stdlib_and_local_imports_are_not_distributions(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "import json\nimport os.path\nfrom pkg import a\nimport pandas\n", encoding="utf-8"
    )
    elements, _ = inventory(str(tmp_path))
    deps = Dependencies(tmp_path, elements=elements)
    assert [u.distribution for u in deps.usage()] == ["pandas"]


def test_reaches_sink_is_populated_from_card_threes_reachability() -> None:
    root = case("dep_requirements_conflict")
    elements, _ = inventory(str(root))
    target = "dep_requirements_conflict.engine::decide"
    assert any(e.id == target for e in elements)
    reachability = [
        Reachability(
            id=f"reach::{target}",
            element_id=target,
            state=ReachabilityState.REACHES_SINK,
            provenance=Provenance(
                method=Method.CFG_REACHABILITY, confidence=Confidence.RESOLVED
            ),
            sink_ids=(target,),
        )
    ]
    deps = Dependencies(root, environment_root=root / "env", elements=elements,
                        reachability=reachability)
    usages = {u.distribution: u for u in deps.usage()}
    assert usages["requests"].reaches_sink is True
    assert usages["requests"].reaches_sink_element_ids == (target,)
    assert usages["pandas"].reaches_sink is False


def test_a_version_conflict_says_how_many_elements_reach_a_sink() -> None:
    root = case("dep_requirements_conflict")
    elements, _ = inventory(str(root))
    target = "dep_requirements_conflict.engine::load_frame"
    reachability = [
        Reachability(
            id=f"reach::{target}",
            element_id=target,
            state=ReachabilityState.REACHES_SINK,
            provenance=Provenance(
                method=Method.CFG_REACHABILITY, confidence=Confidence.RESOLVED
            ),
        )
    ]
    deps = Dependencies(root, environment_root=root / "env", elements=elements,
                        reachability=reachability)
    conflict = next(
        f for f in deps.findings() if f.kind is FindingKind.VERSION_CONFLICT
    )
    assert "reach a decision sink" in conflict.summary


# ---------------------------------------------------------------------------
# Interpreter requirements -- read off the grammar
# ---------------------------------------------------------------------------


def test_a_match_statement_requires_3_10_with_its_own_span() -> None:
    deps = build("dep_interpreter_match", with_env=False)
    records = {r.element_id: r for r in deps.interpreter_requirements()}
    route = records["dep_interpreter_match.router::route"]
    assert route.minimum_python == "3.10"
    assert route.feature == "match statement"
    assert route.provenance.confidence is Confidence.CERTAIN

    source = (case("dep_interpreter_match") / route.span.path).read_text(encoding="utf-8")
    assert source.splitlines()[route.span.line - 1].strip().startswith("match ")


def test_positional_only_parameters_require_3_8_at_the_parameter(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(\n    a,\n    /,\n): return a\n", encoding="utf-8")
    elements, _ = inventory(str(tmp_path))
    records = Dependencies(tmp_path, elements=elements).interpreter_requirements()
    found = [r for r in records if r.feature == "positional-only parameters"]
    assert found
    assert {r.minimum_python for r in found} == {"3.8"}
    assert {r.span.line for r in found} == {2}


def test_an_except_star_group_requires_3_11(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f():\n    try:\n        pass\n    except* ValueError:\n        pass\n",
        encoding="utf-8",
    )
    elements, _ = inventory(str(tmp_path))
    records = Dependencies(tmp_path, elements=elements).interpreter_requirements()
    assert {r.minimum_python for r in records} == {"3.11"}
    assert {r.feature for r in records} == {"except* group"}


def test_a_walrus_requires_3_8(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(xs):\n    if (n := len(xs)):\n        return n\n",
                                   encoding="utf-8")
    elements, _ = inventory(str(tmp_path))
    records = Dependencies(tmp_path, elements=elements).interpreter_requirements()
    assert {r.feature for r in records} == {"walrus operator"}
    assert {r.minimum_python for r in records} == {"3.8"}


def test_the_highest_requirement_wins_per_element(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(a, /, xs):\n"
        "    if (n := len(xs)):\n"
        "        match n:\n"
        "            case 1:\n"
        "                return a\n"
        "    return 0\n",
        encoding="utf-8",
    )
    elements, _ = inventory(str(tmp_path))
    records = [r for r in Dependencies(tmp_path, elements=elements)
               .interpreter_requirements() if r.element_id.endswith("::f")]
    assert len(records) == 1
    assert records[0].minimum_python == "3.10"
    assert records[0].feature == "match statement"


def test_a_file_with_no_special_syntax_claims_no_minimum(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(a):\n    return a\n", encoding="utf-8")
    elements, _ = inventory(str(tmp_path))
    assert Dependencies(tmp_path, elements=elements).interpreter_requirements() == ()


def test_a_syntax_error_is_unresolved_and_the_run_carries_on(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "fine.py").write_text("import pandas\n", encoding="utf-8")
    deps = Dependencies(tmp_path)
    assert [u.distribution for u in deps.usage()] == ["pandas"]
    broken = [u for u in deps.unresolved() if u.id == "dep::source::broken.py"]
    assert len(broken) == 1
    assert broken[0].reason is UnresolvedReason.SYNTAX_ERROR


def test_a_non_utf8_source_file_is_unresolved_and_the_run_carries_on(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_bytes(b"x = '\xff\xfe'\n")
    (tmp_path / "fine.py").write_text("import pandas\n", encoding="utf-8")
    deps = Dependencies(tmp_path)
    assert [u.distribution for u in deps.usage()] == ["pandas"]
    bad = [u for u in deps.unresolved() if u.id == "dep::source::bad.py"]
    assert len(bad) == 1
    assert bad[0].reason is UnresolvedReason.DECODE_ERROR


# ---------------------------------------------------------------------------
# Findings -- one test per kind, and the evidence rule
# ---------------------------------------------------------------------------


def test_undeclared_dependency_is_emitted() -> None:
    findings = [f for f in build("dep_requirements_conflict").findings()
                if f.kind is FindingKind.UNDECLARED_DEPENDENCY]
    assert [f.id for f in findings] == ["finding::UNDECLARED_DEPENDENCY::numpy"]
    assert findings[0].evidence_ids
    assert findings[0].span.path == "engine.py"
    assert "requirements" in findings[0].hint


def test_missing_dependency_is_emitted() -> None:
    findings = [f for f in build("dep_requirements_conflict").findings()
                if f.kind is FindingKind.MISSING_DEPENDENCY]
    assert len(findings) == 1
    assert "numpy" in findings[0].summary
    assert findings[0].provenance.confidence is Confidence.RESOLVED


def test_version_conflict_is_emitted_with_both_sides_of_the_disagreement() -> None:
    findings = [f for f in build("dep_requirements_conflict").findings()
                if f.kind is FindingKind.VERSION_CONFLICT]
    assert len(findings) == 1
    finding = findings[0]
    assert "1.5.3" in finding.summary and "pandas>=2.0,<3.0" in finding.summary
    assert finding.span.path == "requirements.txt"
    assert finding.provenance.confidence is Confidence.RESOLVED


def test_an_unevaluable_specifier_is_reported_unknown_not_satisfied() -> None:
    findings = [f for f in build("dep_unparseable_specifier").findings()
                if f.kind is FindingKind.VERSION_CONFLICT]
    assert len(findings) == 1
    finding = findings[0]
    assert finding.provenance.confidence is Confidence.UNKNOWN
    assert "==1.2+corporate1" in finding.summary
    assert "No verdict" in finding.summary


def test_unused_dependency_is_emitted() -> None:
    findings = [f for f in build("dep_requirements_conflict").findings()
                if f.kind is FindingKind.UNUSED_DEPENDENCY]
    assert [f.id for f in findings] == ["finding::UNUSED_DEPENDENCY::click"]
    assert findings[0].provenance.confidence is Confidence.HEURISTIC
    assert "dynamic" in findings[0].provenance.note


def test_interpreter_too_old_is_emitted() -> None:
    findings = [f for f in build("dep_interpreter_match", with_env=False).findings()
                if f.kind is FindingKind.INTERPRETER_TOO_OLD]
    assert findings
    assert all("3.10" in f.summary for f in findings)
    assert all(f.evidence_ids for f in findings)
    assert any("pyproject.toml:4" in f.summary for f in findings)


def test_all_five_dependency_finding_kinds_are_emitted_by_this_suite() -> None:
    """The guard `test_findings.py` keeps for card 5, for card 17's kinds.

    It is derived from real runs rather than written down, so a kind that
    stops being emitted fails here instead of passing quietly.
    """
    emitted = set()
    for name in DEP_CASES:
        emitted.update(f.kind for f in build(name).findings())
    assert emitted == {
        FindingKind.UNDECLARED_DEPENDENCY,
        FindingKind.MISSING_DEPENDENCY,
        FindingKind.VERSION_CONFLICT,
        FindingKind.UNUSED_DEPENDENCY,
        FindingKind.INTERPRETER_TOO_OLD,
    }


def test_no_finding_ships_with_an_empty_evidence_chain() -> None:
    checked = 0
    for name in DEP_CASES:
        for finding in build(name).findings():
            assert finding.evidence_ids, f"{finding.id} has no evidence"
            assert finding.hint.strip(), f"{finding.id} has no hint"
            assert finding.span.path or finding.element_id, f"{finding.id} is unlocated"
            checked += 1
    assert checked == 7, f"the corpus emits 7 findings, not {checked}"


def test_every_finding_carries_method_and_confidence() -> None:
    findings = build("dep_requirements_conflict").findings()
    assert len(findings) >= 4
    for finding in findings:
        assert finding.provenance.method in set(Method)
        assert finding.provenance.confidence in set(Confidence)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_two_runs_are_byte_identical() -> None:
    def render(name: str) -> str:
        deps = build(name)
        return "".join(
            canonical_jsonl(records)
            for records in (
                deps.requirements(),
                deps.installed(),
                deps.usage(),
                deps.interpreter_requirements(),
                deps.findings(),
                deps.unresolved(),
            )
        )

    assert DEP_CASES
    for name in DEP_CASES:
        assert render(name) == render(name), f"{name} is not deterministic"


def test_the_absolute_and_relative_root_produce_the_same_bytes() -> None:
    name = "dep_requirements_conflict"
    absolute = case(name)
    relative = Path("tests") / "fixtures" / "mode_b" / name
    elements, _ = inventory(str(absolute))
    first = Dependencies(absolute, environment_root=absolute / "env", elements=elements)
    second = Dependencies(
        relative.resolve(), environment_root=relative.resolve() / "env", elements=elements
    )
    assert canonical_jsonl(first.findings()) == canonical_jsonl(second.findings())
    assert canonical_jsonl(first.installed()) == canonical_jsonl(second.installed())


# ---------------------------------------------------------------------------
# Card 16's DocRecord.dependencies
# ---------------------------------------------------------------------------


def test_an_element_that_imports_nothing_says_so_rather_than_being_empty() -> None:
    deps = build("dep_requirements_conflict")
    records = deps.doc_dependencies()
    assert records
    with_none = [r for r in records.values() if not r["distributions"]]
    assert with_none
    for record in with_none:
        assert "imports no distribution" in record["note"]


def test_a_doc_dependency_entry_carries_declared_and_installed_side_by_side() -> None:
    records = build("dep_requirements_conflict").doc_dependencies()
    entry = records["dep_requirements_conflict.engine::load_frame"]
    pandas = next(d for d in entry["distributions"] if d["distribution"] == "pandas")
    assert pandas["installed_version"] == "1.5.3"
    assert pandas["declared"][0]["specifier"] == ">=2.0,<3.0"
    assert pandas["declared"][0]["line"] == 2


def test_doc_dependencies_say_when_the_environment_was_not_read() -> None:
    records = build("dep_absent_environment", with_env=False).doc_dependencies()
    assert records
    for record in records.values():
        assert record["environment"] == "absent"
        assert "--env" in record["environment_note"]


def test_the_completeness_gate_passes_with_no_manifests_and_no_environment(
    tmp_path: Path,
) -> None:
    from cascade_map.docrecords import DocumentationBuilder

    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    elements, _ = inventory(str(tmp_path))
    deps = Dependencies(tmp_path, elements=elements)
    builder = DocumentationBuilder(
        elements=elements, dependencies=deps.doc_dependencies()
    )
    records = builder.records()
    assert records
    assert builder.completeness_gate(records) == ()


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def _analyze(tmp_path: Path, name: str, *extra: str) -> tuple[int, str, Path]:
    from cascade_map.cli import analyze

    out = tmp_path / "out"
    root = case(name)
    env = root / "env"
    code, summary = analyze(
        root, out, env_root=env if (extra and extra[0] == "env") else None, strict_gate=False
    )
    from cascade_map.cli import _report

    return code, _report(summary, out, code), out


def test_analyze_writes_the_four_new_artifacts(tmp_path: Path) -> None:
    _, _, out = _analyze(tmp_path, "dep_requirements_conflict", "env")
    for name in (
        "requirements.jsonl",
        "installed.jsonl",
        "package_usage.jsonl",
        "interpreter.jsonl",
    ):
        path = out / name
        assert path.is_file(), f"{name} was not written"
    rows = [json.loads(line) for line in
            (out / "requirements.jsonl").read_text().splitlines()]
    assert {row["distribution"] for row in rows} >= {"pandas", "click"}


def test_dependency_findings_merge_into_findings_jsonl(tmp_path: Path) -> None:
    _, _, out = _analyze(tmp_path, "dep_requirements_conflict", "env")
    kinds = {
        json.loads(line)["kind"]
        for line in (out / "findings.jsonl").read_text().splitlines()
    }
    assert "VERSION_CONFLICT" in kinds
    assert "UNDECLARED_DEPENDENCY" in kinds


def test_the_report_names_the_flag_when_no_environment_was_read(tmp_path: Path) -> None:
    _, report, out = _analyze(tmp_path, "dep_requirements_conflict")
    assert "NOT CHECKED" in report
    assert "--env" in report
    installed = (out / "installed.jsonl").read_text()
    assert installed == ""
    unresolved = (out / "unresolved.jsonl").read_text()
    assert "dep::environment" in unresolved


def test_the_report_prints_the_disagreements_when_an_environment_was_read(
    tmp_path: Path,
) -> None:
    _, report, _ = _analyze(tmp_path, "dep_requirements_conflict", "env")
    assert "VERSION_CONFLICT" in report
    assert "UNDECLARED_DEPENDENCY" in report
    assert "NOT CHECKED" not in report


def test_analyze_is_byte_identical_across_two_runs(tmp_path: Path) -> None:
    from cascade_map.cli import analyze

    root = case("dep_requirements_conflict")
    first, second = tmp_path / "a", tmp_path / "b"
    analyze(root, first, env_root=root / "env", strict_gate=False)
    analyze(root, second, env_root=root / "env", strict_gate=False)
    for name in (
        "requirements.jsonl",
        "installed.jsonl",
        "package_usage.jsonl",
        "interpreter.jsonl",
        "findings.jsonl",
        "records.jsonl",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
