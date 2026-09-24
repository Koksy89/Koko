"""CASCADE-MAP — a static and runtime map of a Python decision engine."""

# THE single source of truth for the version. It lives here, as a plain
# literal, because the amalgamated single file has no VERSION file beside it
# to read at runtime -- it is one file by definition. `tools/amalgamate.py`
# reads THIS literal (with `ast`, never by importing) and bakes it into the
# single file, so the two cannot report different versions.
#
# The repository's VERSION file is a convenience mirror. It must agree, and
# tests/test_version_is_single_sourced.py fails the build if it does not.
# That test exists because the two silently disagreed once: VERSION was
# bumped to 1.1.0 while this stayed 1.0.0, so the single file and the package
# reported DIFFERENT versions for identical code. CHANGELOG.md explains why
# that is worse than it sounds -- `track` uses the tool version to decide
# whether a change belongs to the owner's engine or to this tool, so two
# tools disagreeing about their own version can misattribute an entire
# engine's worth of change.
__version__ = "1.1.0"
