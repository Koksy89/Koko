"""The shipped file must be in version control.

`dist/` is gitignored with an exception for the deliverable. That exception
once named an exact filename, so renaming the build output silently untracked
it: the one file an owner downloads stopped being committed, `git status` said
"clean", and nothing failed. This fails instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "dist"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="not a git checkout")
def test_every_built_file_in_dist_is_tracked() -> None:
    built = sorted(p.name for p in DIST.glob("*.py"))
    if not built:
        pytest.skip("nothing built yet")
    tracked = set(_git("ls-files", "dist/").split())
    for name in built:
        assert f"dist/{name}" in tracked, (
            f"dist/{name} is NOT tracked by git. It is the file an owner "
            f"downloads and runs, and it is being ignored -- check the "
            f"exception pattern in .gitignore. A rename did this once before."
        )
