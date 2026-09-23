"""`doctor` writes one file the owner can send without reading it first.

Two things have to hold. It must contain the measurements that answer "why
is this slow and do workers help", and it must contain NOTHING from the
target's contents -- no source, no docstrings, no literals. The second is
asserted against a target planted with distinctive secrets, because "I
believe it only writes counts" is the kind of claim that stays true until
someone adds a field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cascade_map.cli import main
from cascade_map.doctor import (
    DoctorResult,
    machine_shape,
    render,
    run_doctor,
    utc_stamp,
    write_report,
)

SECRET_FN = "zzq_secret_function_name"
SECRET_STRING = "SUPER-SECRET-API-TOKEN-9f3a"
SECRET_DOC = "This docstring must never leave the machine."


def _planted_target(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "__init__.py").write_text("", encoding="utf-8")
    (base / "engine.py").write_text(
        f'"""{SECRET_DOC}"""\n'
        f"TOKEN = {SECRET_STRING!r}\n"
        f"def {SECRET_FN}(a):\n"
        f"    return a + 1\n",
        encoding="utf-8",
    )
    for i in range(4):
        (base / f"mod{i}.py").write_text(
            "\n".join(f"def f{i}_{j}(a):\n    return a + {j}\n" for j in range(30)),
            encoding="utf-8",
        )
    return base


def test_writes_a_log_and_a_json_named_for_a_utc_timestamp(tmp_path: Path) -> None:
    result = run_doctor(_planted_target(tmp_path / "t"), scaling_points=(1, 2))
    out = tmp_path / "out"
    log_path = write_report(result, out, stamp="20260101T000000Z")

    assert log_path == out / "metatron_doctor_20260101T000000Z.log"
    assert log_path.exists()
    json_path = out / "metatron_doctor_20260101T000000Z.json"
    assert json_path.exists(), "the same data as JSON must sit beside the log"
    json.loads(json_path.read_text(encoding="utf-8"))


def test_utc_stamp_is_utc_and_sortable() -> None:
    stamp = utc_stamp(0.0)
    assert stamp == "19700101T000000Z"
    assert utc_stamp(0.0) < utc_stamp(1.0e9)


def test_the_report_contains_no_content_from_the_target(tmp_path: Path) -> None:
    """The safety claim, tested rather than asserted in a comment."""
    target = _planted_target(tmp_path / "t")
    result = run_doctor(target, scaling_points=(1, 2))
    out = tmp_path / "out"
    write_report(result, out, stamp="20260101T000000Z")

    for path in sorted(out.iterdir()):
        text = path.read_text(encoding="utf-8")
        for secret in (SECRET_FN, SECRET_STRING, SECRET_DOC):
            assert secret not in text, f"{path.name} leaked {secret!r}"
        assert "def " not in text, f"{path.name} appears to contain source"


def test_the_report_carries_the_measurements_it_exists_for(tmp_path: Path) -> None:
    result = run_doctor(_planted_target(tmp_path / "t"), scaling_points=(1, 2))
    text = render(result)

    assert "MACHINE" in text and "python_version" in text
    assert "cpu_usable" in text
    assert "CACHE" in text and ("cold" in text or "yes" in text)
    assert "STAGE TIMINGS" in text and "inventory" in text
    assert "WORKER SCALING" in text
    assert "--workers 1" in text and "--workers 2" in text
    assert "ERRORS" in text
    assert result.elements > 0
    assert result.file_count >= 5
    assert [p.workers_requested for p in result.scaling] == [1, 2]


def test_machine_shape_reports_usable_cores_not_just_cpu_count() -> None:
    shape = machine_shape()
    assert shape["cpu_usable"] >= 1
    assert shape["workers_auto_would_be"] >= 1
    assert shape["python_version"]


def test_measures_its_own_source_when_no_target_is_given(tmp_path: Path) -> None:
    """`doctor` with no --target measures metatron itself, which is the run
    the owner does first."""
    result = run_doctor(None, scaling_points=(1,))
    assert result.elements > 0
    assert "metatron" in result.target_kind
    assert not result.errors, result.errors


def test_errors_are_recorded_in_the_report_not_swallowed(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    result = run_doctor(missing, scaling_points=(1,))
    assert result.errors
    assert "not a directory" in render(result)


def test_an_empty_result_still_renders(tmp_path: Path) -> None:
    """A doctor run that measured nothing must still produce a readable file
    saying so -- a blank file is how an owner sends nothing and thinks they
    sent something."""
    text = render(DoctorResult(target="x", target_kind="y"))
    assert "not measured" in text
    assert "ERRORS" in text


def test_cli_prints_the_full_path_on_the_last_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _planted_target(tmp_path / "t")
    out = tmp_path / "out"
    code = main(
        ["doctor", "--out", str(out), "--target", str(target), "--workers", "1"]
    )
    assert code == 0
    printed = capsys.readouterr().out.strip().splitlines()
    last = printed[-1]
    assert Path(last).is_absolute()
    assert Path(last).exists()
    assert Path(last).suffix == ".log"


def test_cli_rejects_a_target_that_is_not_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["doctor", "--out", str(tmp_path / "o"), "--target", str(tmp_path / "nope")])
    assert code != 0
    assert "not a directory" in capsys.readouterr().err
