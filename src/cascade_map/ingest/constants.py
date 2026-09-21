"""Tunable constants for card 1 ingestion.

Every threshold here is an approximation the builder chose in the absence of a
contract field for it. Reported in the card 1 build report; not binding.
"""

from __future__ import annotations

# A string/bytes literal at or above this size (in bytes) is recorded as an
# opaque BLOB element instead of being inlined into a docstring/signature or
# left invisible. Calibrated well below the ~1 MB blobs the target embeds, and
# comfortably above ordinary docstrings.
BLOB_THRESHOLD_BYTES = 1024

# literal_value (Element) is capped at the same boundary as BLOB_THRESHOLD_BYTES:
# a str/bytes literal at or above that size is already absorbed into a BLOB
# element instead of an ASSIGNMENT one (see python_module._is_big_constant),
# so this is a defensive, explicit second cap rather than a reachable path --
# literal_value must never become a way to smuggle blob content into the
# artifact under a different field name.
LITERAL_VALUE_CAP_BYTES = BLOB_THRESHOLD_BYTES

# A docstring longer than this is not copied into Element.docstring verbatim
# (avoids duplicating multi-megabyte text into every consumer of elements.jsonl).
# The underlying literal is still visited by blob detection and, if it clears
# BLOB_THRESHOLD_BYTES, recorded as its own BLOB element.
DOCSTRING_INLINE_CAP = 2000

# A source file at or above this size is not parsed: emitted as a TOO_LARGE
# Unresolved record instead. Chosen well above any single file observed in the
# target profile (~14 MB across the whole tree, ~3.9 MB of that in blobs).
MAX_FILE_BYTES = 16 * 1024 * 1024

# Directory names never descended into.
EXCLUDED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".venv-target",
        "venv",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cascade_map",
    }
)

# Never target content: the fixture corpus's own sidecar files (FIXTURES.md),
# sitting beside source files inside mode_b/mode_a case directories --
# `expected.json` (the hand-written expectation) and `spans.json` (auxiliary
# span/byte-size metadata some cases carry alongside it). A real target tree
# has no reason to contain either exact name; excluding them keeps fixture
# metadata out of the inventory it is grading. Fixture *source* config files
# used by a case under test (e.g. `wiring.json`, `config.json`) are not on
# this list and are ingested normally.
EXCLUDED_FILE_NAMES = frozenset({"expected.json", "spans.json"})

# Non-Python config/data file extensions ingested as DATA_FILE (+ CONFIG_KEY
# for the structured formats: JSON, YAML, INI).
CONFIG_EXTENSIONS = {
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ini": "ini",
    ".cfg": "ini",
    ".csv": "csv",
}
