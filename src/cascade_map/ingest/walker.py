"""Walks a target tree and classifies files.

Never opens an interpreter, never imports anything under the walked root --
this module only touches `pathlib` and `os.scandir`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .constants import CONFIG_EXTENSIONS, EXCLUDED_DIR_NAMES, EXCLUDED_FILE_NAMES


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    path: Path
    """As constructed from the walk -- carries whatever prefix `root` had.
    Used verbatim as `SourceSpan.path` (POSIX-ified)."""

    kind: str
    """"python" | "json" | "yaml" | "ini" | "csv" """


def module_dotted_name(root: Path, file_path: Path) -> str:
    """Dotted module name for a `.py` file under `root`.

    Computed relative to `root`'s *parent*, so `root`'s own basename becomes
    the top-level package/module component -- pointing the tool at
    `target_engine/` makes `target_engine` the root of every dotted name,
    exactly like pointing it at a single-file case makes that directory's
    name the module name.
    """
    base = root.parent if root.parent != root else root
    rel = file_path.relative_to(base)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def walk(root: str) -> Iterator[DiscoveredFile]:
    """Yield every relevant file under `root`, sorted for determinism.

    Sorting matters: ordinal disambiguation and ID-collision detection must
    see files in the same order on every run.
    """
    root_path = Path(root)
    found: list[Path] = []
    _walk_dir(root_path, found)
    found.sort(key=lambda p: p.as_posix())
    for path in found:
        if path.name in EXCLUDED_FILE_NAMES:
            continue
        suffix = path.suffix.lower()
        if suffix == ".py":
            yield DiscoveredFile(path=path, kind="python")
        elif suffix in CONFIG_EXTENSIONS:
            yield DiscoveredFile(path=path, kind=CONFIG_EXTENSIONS[suffix])


def _walk_dir(directory: Path, out: list[Path]) -> None:
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name in EXCLUDED_DIR_NAMES or entry.name.startswith("."):
                continue
            _walk_dir(entry, out)
        elif entry.is_file():
            out.append(entry)
