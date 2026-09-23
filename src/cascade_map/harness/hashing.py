"""Deterministic identifiers the harness needs before it will run anything.

Target content hashes, the graph hash they must match, and the run ID. All
three are pure functions of their inputs so that two runs against the same
target, scenario and config produce byte-identical output -- the replay
guarantee starts here, not just in card 12's event log.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from cascade_map.contracts import canonical_dumps

#: Never part of the target's own content; walking into these would make the
#: hash depend on incidental local state (a cache directory, a VCS folder)
#: rather than on the target itself.
_EXCLUDED_DIR_NAMES = frozenset(
    {"__pycache__", ".git", ".hg", ".svn", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)


def is_target_content(relative_posix: str) -> bool:
    """Is this POSIX-relative path part of the target's own content?

    The one place the exclusion rule lives. `cli` needs it to compare a
    manifest written by `analyze` -- whose own walk skips less -- against what
    this module hashes, and a second copy of the rule is a second answer to
    "what is the target", which is exactly what the graph hash exists to pin
    down.
    """
    return not any(part in _EXCLUDED_DIR_NAMES for part in PurePosixPath(relative_posix).parts)


def compute_target_hashes(target_root: Path) -> dict[str, str]:
    """SHA-256 of every file under *target_root*, keyed by POSIX-relative path.

    A missing root hashes to an empty mapping rather than raising: the caller
    (``Harness.start``) turns that into a named refusal, which is the correct
    place for a human-readable reason to live.
    """
    root = Path(target_root)
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if any(part in _EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def compute_graph_hash(target_hashes: dict[str, str]) -> str:
    """The hash a completed Mode B graph is stamped with.

    Purely a function of the target's own content hashes, so the harness can
    recompute "what the graph should say" without reading the graph's own
    on-disk format -- whose exact shape (``manifest.json``) is not part of
    the binding contract card 11 implements against. See the harness build
    report for the resulting gap.
    """
    payload = canonical_dumps({"target_hashes": target_hashes})
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def config_fingerprint(
    declared_process_names: frozenset[str],
    client_names: frozenset[str],
    env_passthrough: frozenset[str],
) -> str:
    """A deterministic summary of the parts of a run config that affect what
    a run is allowed to do, for folding into the run ID."""
    payload = canonical_dumps(
        {
            "declared_process_names": sorted(declared_process_names),
            "client_names": sorted(client_names),
            "env_passthrough": sorted(env_passthrough),
        }
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def compute_run_id(scenario: str, graph_hash: str, config_fp: str) -> str:
    """A run ID that is a pure function of scenario, graph and config.

    Deterministic, not random: the same scenario against the same graph with
    the same declared controls always gets the same run ID, which is what
    lets a replay of a recorded run be compared byte-for-byte against a fresh
    one instead of merely "close."
    """
    payload = canonical_dumps({"scenario": scenario, "graph_hash": graph_hash, "config": config_fp})
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
    return f"run_{digest[:16]}"
