"""Card 17 round 6 -- the per-file source limit, and what it must say.

4,000,000 bytes was chosen against a fixture corpus. The target this tool
was built for is 14,804,021 bytes in one file, so on the owner's real run
the scanner skipped the engine itself and its imports and interpreter
requirements were never read.

The limit is now set from a measurement of that very file rather than from
a guess -- see `MAX_SOURCE_BYTES` for the numbers and the run they came
from -- and where a limit still bites, the record states its own number,
names the flag that raises it, and is scoped to the one file.

Nothing here writes into `tests/fixtures/`.
"""

from __future__ import annotations

from pathlib import Path

from cascade_map.dependencies import MAX_SOURCE_BYTES, Dependencies

#: The owner's engine, in bytes. The default limit must cover a target of
#: this shape, because a scanner that cannot read the target is not a
#: scanner.
_REAL_SINGLE_FILE_ENGINE_BYTES = 14_804_021


def _write_target(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    module = root / "engine.py"
    module.write_text(
        "import json\n"
        "import numpy\n"
        "from collections import OrderedDict\n"
        "\n"
        "def go(payload):\n"
        "    return numpy.asarray(json.loads(payload))\n"
        + "# padding so this file is comfortably over a tiny limit\n" * 8,
        encoding="utf-8",
    )
    return module


def test_default_limit_covers_a_real_single_file_engine() -> None:
    assert MAX_SOURCE_BYTES >= _REAL_SINGLE_FILE_ENGINE_BYTES, (
        "the default limit must read the target this tool exists for; "
        f"{MAX_SOURCE_BYTES} < {_REAL_SINGLE_FILE_ENGINE_BYTES}"
    )


def test_a_file_under_the_limit_has_its_imports_read(tmp_path: Path) -> None:
    module = _write_target(tmp_path / "target")
    assert module.stat().st_size < MAX_SOURCE_BYTES
    deps = Dependencies(tmp_path / "target")
    usage = {record.distribution for record in deps.usage()}
    assert "numpy" in usage
    assert not [u for u in deps.unresolved() if u.id.startswith("dep::source::")]


def test_a_file_over_the_limit_is_skipped_per_file_and_says_so(tmp_path: Path) -> None:
    module = _write_target(tmp_path / "target")
    size = module.stat().st_size
    deps = Dependencies(tmp_path / "target", max_source_bytes=size - 1)
    deps.usage()
    skips = [u for u in deps.unresolved() if u.id.startswith("dep::source::")]
    assert len(skips) == 1
    record = skips[0]
    assert record.span.path == "engine.py"
    assert record.reason.value == "TOO_LARGE"
    text = record.description
    # The limit states its own number...
    assert str(size - 1) in text
    # ...and the flag that raises it...
    assert "--max-source-mb" in text
    # ...and confines the claim to this file and this scan. The banner that
    # read "this map does not describe it" was built out of a sentence that
    # did not.
    assert "engine.py" in text
    assert "imports and interpreter requirements were not read" in text
    assert "this file and this scan only" in text


def test_the_skip_does_not_stop_the_rest_of_the_scan(tmp_path: Path) -> None:
    """One oversized file is one skipped file, not a dead scan."""
    root = tmp_path / "target"
    _write_target(root)
    (root / "small.py").write_text("import json\n", encoding="utf-8")
    big = (root / "engine.py").stat().st_size
    deps = Dependencies(root, max_source_bytes=big - 1)
    usage = {record.distribution for record in deps.usage()}
    assert "numpy" not in usage          # the skipped file's import
    skipped = [u.span.path for u in deps.unresolved() if u.id.startswith("dep::source::")]
    assert skipped == ["engine.py"]      # and only that file


def test_raising_the_limit_reads_the_file_that_was_skipped(tmp_path: Path) -> None:
    root = tmp_path / "target"
    module = _write_target(root)
    size = module.stat().st_size
    tight = Dependencies(root, max_source_bytes=size - 1)
    assert "numpy" not in {r.distribution for r in tight.usage()}
    loose = Dependencies(root, max_source_bytes=size)
    assert "numpy" in {r.distribution for r in loose.usage()}
    assert not [u for u in loose.unresolved() if u.id.startswith("dep::source::")]
