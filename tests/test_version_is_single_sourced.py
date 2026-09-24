"""The version must be one fact, not three that can drift apart.

It was three. `VERSION` said 1.1.0, `cascade_map.__version__` said 1.0.0 and
`pyproject.toml` said 0.0.0, so the single file and the package reported
different versions for byte-identical behaviour. `track` attributes a change
to the engine or to the tool by comparing tool versions, so this is not
cosmetic: two builds disagreeing about their own version can misattribute a
whole engine's worth of change.
"""

from __future__ import annotations

from pathlib import Path

from cascade_map import __version__

REPO = Path(__file__).resolve().parents[1]


def test_version_file_matches_the_package() -> None:
    version_file = REPO / "VERSION"
    if not version_file.exists():  # installed without the repo beside it
        return
    on_disk = version_file.read_text(encoding="utf-8").strip()
    assert on_disk == __version__, (
        f"VERSION says {on_disk!r} but cascade_map.__version__ says "
        f"{__version__!r}. The amalgamated single file takes the package's "
        f"value, so leaving these apart ships a tool that disagrees with "
        f"itself about which version produced a map."
    )


def test_changelog_documents_this_version() -> None:
    changelog = REPO / "CHANGELOG.md"
    if not changelog.exists():
        return
    text = changelog.read_text(encoding="utf-8")
    assert f"## {__version__}" in text, (
        f"CHANGELOG.md has no '## {__version__}' section. Every version an "
        f"owner can run must say what changed in it, or the version number "
        f"tells them nothing."
    )
