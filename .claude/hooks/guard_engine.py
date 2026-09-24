#!/usr/bin/env python3
"""Block accidental execution of, or writes to, the analysis target.

CASCADE-MAP reads ``target_engine/`` and ``target_versions/`` as text. It must
never execute, import, exec, eval or unpickle them, and never write to them.
The only sanctioned execution path is the Mode A harness command
(``metatron trace``, or the single file run as ``python3 <built file> trace``),
which the owner approves explicitly.

This runs as a ``PreToolUse`` hook. It reads the hook payload on stdin and
either stays silent (deferring to the normal permission flow) or prints a
``deny`` decision.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This is a tripwire, not a sandbox. It inspects a command *as text* before the
shell sees it. Shell syntax is Turing-complete, so a determined caller can
always defeat static inspection -- an indirection through a variable, a script
that is itself innocuous but invokes the target, a path assembled at runtime.
The real isolation guarantee belongs to card 11's harness, which controls the
process, its filesystem and its network.

What this hook does buy you is the thing that actually goes wrong in practice:
a reflexive ``python target_engine/run_m5.py`` typed without thinking. It stops
that, loudly, before the process starts.

DESIGN NOTES
------------
* ``sed`` counts as read-only because ``sed -n`` is the sanctioned way to
  read a slice of a target file; ``sed -i`` is denied separately. ``awk``
  and ``perl`` are deliberately *not* on that list -- both can execute
  arbitrary code, so they fall through to the default-deny branch.
* Known gap: GNU sed's obscure ``e`` command can execute a shell command
  from inside a script. The guard does not parse sed scripts. Isolation
  proper is card 11's job; this is a tripwire.
* Read access is deliberately untouched. ``grep -rn ... target_engine/`` and
  ``sed -n '1,50p' target_engine/foo.py`` are how every static card does its
  work, so anything whose command head is a read-only tool passes even when it
  names a protected path.
* Denials are driven by the *command head* of each shell segment, not by any
  token appearing anywhere. That is what keeps ``grep -rn "python"
  target_engine/`` from being flagged: ``python`` there is a search pattern,
  not a program.
* ``cd`` into a protected directory is tracked across ``&&``-joined segments,
  so ``cd target_engine && python run_m5.py`` is caught even though the second
  segment never names the directory.
* On an internal error the hook fails *closed* for anything mentioning a
  protected path and *open* otherwise, so a bug here can never silently permit
  the one thing it exists to prevent, nor block unrelated work.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from typing import Any, Iterable

PROTECTED_DIRS: tuple[str, ...] = ("target_engine", "target_versions")
TARGET_VENV: str = ".venv-target"

#: Programs that run code. Naming a protected path on one of these is a denial.
EXECUTORS: frozenset[str] = frozenset(
    {
        "python", "python2", "python3", "py", "pypy", "pypy3",
        "ipython", "ipython3", "jupyter", "jupyter-notebook", "jupyter-lab",
        "pytest", "py.test", "tox", "nox", "unittest", "nosetests",
        "pip", "pip2", "pip3", "easy_install",
        "uv", "uvx", "poetry", "pipenv", "conda", "mamba", "hatch", "pdm", "rye",
        "bash", "sh", "zsh", "dash", "ksh", "csh", "tcsh", "fish",
        "source", ".", "eval", "exec",
        "make", "ninja", "just", "task", "invoke", "fab", "doit",
        "gunicorn", "uvicorn", "celery", "flask", "django-admin", "streamlit",
    }
)
# Any python3.X / python3.12 style name is treated as an executor too.
_VERSIONED_PYTHON = re.compile(r"^(python|pypy)\d+(\.\d+)*$")

#: Programs that change files. Pointing one at a protected path is a denial.
MUTATORS: frozenset[str] = frozenset(
    {
        "rm", "mv", "cp", "install", "dd", "shred", "truncate", "tee",
        "chmod", "chown", "chgrp", "ln", "mkdir", "rmdir", "touch",
        "patch", "unzip", "tar", "rsync", "sponge",
    }
)

#: Programs that only read. These pass even when they name a protected path.
READ_ONLY: frozenset[str] = frozenset(
    {
        "cat", "head", "tail", "less", "more", "bat", "nl", "sed",
        "grep", "egrep", "fgrep", "rg", "ag", "ack",
        "ls", "find", "fd", "tree", "stat", "file", "realpath", "readlink",
        "wc", "du", "df", "cut", "sort", "uniq", "tr", "comm", "join", "paste",
        "diff", "cmp", "md5sum", "sha1sum", "sha256sum", "shasum", "cksum",
        "xxd", "od", "hexdump", "strings", "basename", "dirname",
        "echo", "printf", "true", "false", "test", "which", "type", "pwd",
        "git", "jq", "yq", "column", "date",
    }
)

#: Wrappers that prefix a real command; strip them and judge what follows.
PREFIX_WRAPPERS: frozenset[str] = frozenset(
    {"env", "nohup", "nice", "ionice", "stdbuf", "time", "command", "builtin",
     "sudo", "doas", "setsid", "unbuffer", "script"}
)
#: Wrappers that take a numeric/flag argument before the real command.
ARG_WRAPPERS: dict[str, int] = {"timeout": 1, "watch": 1, "xargs": 0}

#: Sanctioned Mode A entry point. Only ``trace`` is allowed through.
#:
#: Every name the tool has shipped under is listed, not just the current one.
#: This is an ALLOWLIST, so a name missing here fails SAFE -- the command is
#: judged by the ordinary rules and blocked if it touches the target -- but it
#: still breaks the owner's only legitimate way to run Mode A. That happened:
#: the console script was renamed to ``metatron`` while this still said
#: ``cascade-map``, which silently took Mode A away. Old names are kept so an
#: owner on an older build is not stranded either.
HARNESS_HEADS: frozenset[str] = frozenset(
    {"metatron", "cascade-map", "cascade_map"}
)

#: The single file is run as ``python3 Metatron_Engine_Prototype_v1.py trace``,
#: which is an executor plus a SCRIPT PATH -- it matches neither the console
#: script above nor the ``-m cascade_map`` form. It was recognised by neither
#: until this was added. Matched on the basename only, so the owner may keep
#: the file wherever they like; the stem must still look like this tool, and
#: ``trace`` must still be present.
HARNESS_SCRIPT_STEMS: tuple[str, ...] = (
    "metatron_engine_prototype",
    "metatron_engine",
    "metatron",
    "cascade_map",
)


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(?=\s|$|;|&|\|)")


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies, keeping the command lines that introduce them.

    A heredoc body is data being written, not a command being run. Without this
    the guard denies `cat > docs/NOTES.md <<'EOF'` whenever the prose inside
    happens to name the target -- which, in this project's documentation, is
    constantly.

    The redirect on the introducing line is deliberately kept, so a heredoc
    written *into* a protected path is still caught.

    Known limitation: a left-shift such as `x << y` inside an unquoted command
    would be misread as a heredoc and swallow the rest of the input. The match
    is skipped when it falls inside a quoted string, which covers the realistic
    `python -c '...'` case.
    """
    lines = command.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        terminators = [
            match.group(2)
            for match in _HEREDOC.finditer(line)
            if line[: match.start()].count("'") % 2 == 0
            and line[: match.start()].count('"') % 2 == 0
        ]
        index += 1
        while index < len(lines) and terminators:
            if lines[index].strip() == terminators[0]:
                terminators.pop(0)
            index += 1
    return "\n".join(kept)


def _split_segments(command: str) -> list[str]:
    """Split a command on shell operators, respecting quotes.

    A regex split is not enough: `python -c 'import sys; import engine'` would
    be torn in half at the semicolon inside the string literal, and the half
    naming the target would be judged on its own.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        char = command[i]
        if quote:
            current.append(char)
            if char == "\\" and quote == '"' and i + 1 < len(command):
                current.append(command[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            i += 1
            continue
        two = command[i : i + 2]
        if two in ("&&", "||"):
            segments.append("".join(current))
            current = []
            i += 2
            continue
        if char in ";|\n":
            segments.append("".join(current))
            current = []
            i += 1
            continue
        if char == "&" and command[i - 1 : i] != ">" and two != "&>":
            segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    segments.append("".join(current))
    return segments


_PROTECTED_MENTION = re.compile(
    r"(?<![\w.-])(" + "|".join(re.escape(d) for d in PROTECTED_DIRS) + r")(?![\w-])"
)
_VENV_MENTION = re.compile(r"(?<![\w.-])" + re.escape(TARGET_VENV) + r"(?![\w-])")
_REDIRECT = re.compile(r"(?:\d?>>?|\d?>\|)\s*([^\s;|&]+)")
_SED_INPLACE = re.compile(r"(?:^|\s)-i(?:\.\S*)?(?=\s|$)")


def _basename(token: str) -> str:
    """Program name from argv[0], ignoring any directory part."""
    return os.path.basename(token.strip("'\""))


def _is_executor(head: str) -> bool:
    return head in EXECUTORS or bool(_VERSIONED_PYTHON.match(head))


def _path_is_protected(token: str, base_dir: str) -> bool:
    """True when *token*, read as a path, lands inside a protected directory."""
    raw = token.strip("'\"")
    if not raw:
        return False
    if "=" in raw and not raw.startswith("/"):  # --dir=target_engine/x
        raw = raw.split("=", 1)[1]
    candidates = [raw]
    if not os.path.isabs(raw):
        candidates.append(os.path.join(base_dir, raw))
    for candidate in candidates:
        parts = os.path.normpath(candidate).split(os.sep)
        if any(part in PROTECTED_DIRS or part == TARGET_VENV for part in parts):
            return True
    return False


def _mentions_protected(text: str) -> bool:
    return bool(_PROTECTED_MENTION.search(text))


def _mentions_venv(text: str) -> bool:
    return bool(_VENV_MENTION.search(text))


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop env assignments and wrapper commands to reach the real argv[0]."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):  # FOO=bar cmd
            index += 1
            continue
        name = _basename(token)
        if name in PREFIX_WRAPPERS:
            index += 1
            continue
        if name in ARG_WRAPPERS:
            index += 1 + ARG_WRAPPERS[name]
            # Skip any flags belonging to the wrapper itself.
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        break
    return tokens[index:]


def _inline_scripts(tokens: list[str]) -> Iterable[str]:
    """Command strings carried as arguments (``sh -c``, ``find -exec``)."""
    head = _basename(tokens[0]) if tokens else ""
    for i, token in enumerate(tokens):
        if token == "-c" and head in {"bash", "sh", "zsh", "dash", "ksh", "fish"}:
            if i + 1 < len(tokens):
                yield tokens[i + 1]
        if token in {"-exec", "-execdir", "-ok", "-okdir"}:
            rest = tokens[i + 1:]
            trimmed = [t for t in rest if t not in {";", chr(92) + ";", "+", "{}"}]
            if trimmed:
                yield " ".join(shlex.quote(t) for t in trimmed)


def _inline_scripts_present(argv: list[str]) -> bool:
    """True when this command carries another command as an argument."""
    return any(True for _ in _inline_scripts(argv))


def _check_segment(
    segment: str, base_dir: str, inherited_mention: bool = False
) -> tuple[str | None, str]:
    """Judge one shell segment. Returns (denial reason or None, new base_dir).

    *inherited_mention* carries down into commands passed as arguments, so
    ``find target_engine -exec python {} ;`` knows that the ``python`` it
    recurses into is being pointed at the target.
    """
    tokens = _tokenize(segment)
    if not tokens:
        return None, base_dir

    argv = _strip_wrappers(tokens)
    if not argv:
        return None, base_dir
    head = _basename(argv[0])

    # `cd` moves the directory every later segment is judged against.
    if head == "cd":
        if len(argv) > 1:
            target = argv[1].strip("'\"")
            base_dir = os.path.normpath(
                target if os.path.isabs(target) else os.path.join(base_dir, target)
            )
        return None, base_dir

    # The sanctioned Mode A path. Everything else under cascade-map is ordinary.
    if head in HARNESS_HEADS and "trace" in argv[1:]:
        return None, base_dir
    if _is_executor(head) and argv[1:2] == ["-m"] and argv[2:3] in (
        ["cascade_map"], ["cascade_map.cli"]
    ) and "trace" in argv[3:]:
        return None, base_dir
    # The single file, run by path: `python3 Metatron_Engine_Prototype_v1.py trace`.
    if _is_executor(head) and len(argv) > 2 and "trace" in argv[2:]:
        stem = _basename(argv[1].strip("'\"")).removesuffix(".py").lower()
        if any(stem.startswith(known) for known in HARNESS_SCRIPT_STEMS):
            return None, base_dir

    in_protected_cwd = _path_is_protected(".", base_dir)
    mentions = _mentions_protected(segment) or in_protected_cwd or inherited_mention

    # The target virtualenv has exactly one legitimate use, and it is not here.
    if _mentions_venv(segment) and head not in READ_ONLY:
        return (
            f"Blocked: this command touches {TARGET_VENV}, the interpreter that "
            f"has the engine's dependencies installed. Nothing may run under it "
            f"outside the Mode A harness.",
            base_dir,
        )

    # Running a file that lives inside the target, e.g. ./target_engine/run.sh
    if _path_is_protected(argv[0], base_dir):
        return (
            f"Blocked: '{argv[0]}' lives inside the analysis target. Target code "
            f"is never executed outside the Mode A harness.",
            base_dir,
        )

    if mentions:
        where = f" (working directory is {base_dir})" if in_protected_cwd else ""
        if _is_executor(head):
            return (
                f"Blocked: '{head}' would execute, import or evaluate code from "
                f"the analysis target{where}. Static analysis parses the target "
                f"with `ast`; it never runs it. Read it instead -- `grep -rn`, "
                f"`sed -n`, `cat`.",
                base_dir,
            )
        if head in MUTATORS:
            return (
                f"Blocked: '{head}' would modify the analysis target{where}. "
                f"target_engine/ and target_versions/ are read-only.",
                base_dir,
            )
        if head in {"sed", "perl", "awk"} and _SED_INPLACE.search(segment):
            return (
                f"Blocked: in-place edit of the analysis target. "
                f"target_engine/ and target_versions/ are read-only.",
                base_dir,
            )
        for match in _REDIRECT.finditer(segment):
            if _path_is_protected(match.group(1), base_dir):
                return (
                    f"Blocked: redirecting output into '{match.group(1)}'. "
                    f"target_engine/ and target_versions/ are read-only.",
                    base_dir,
                )
        if head not in READ_ONLY and not _inline_scripts_present(argv):
            return (
                f"Blocked: '{head}' names the analysis target, and it is not on "
                f"the read-only allowlist, so the guard cannot tell whether it "
                f"would run or change target code. If '{head}' only reads, add "
                f"it to READ_ONLY in .claude/hooks/guard_engine.py and say so in "
                f"your report.",
                base_dir,
            )

    # Commands carried as arguments get the same treatment.
    for inner in _inline_scripts(argv):
        reason, _ = _check_segment(inner, base_dir, inherited_mention=mentions)
        if reason:
            return reason, base_dir

    return None, base_dir


def check_bash(command: str, cwd: str) -> str | None:
    """Return a denial reason for *command*, or None to let it through."""
    base_dir = cwd or os.getcwd()
    for segment in _split_segments(_strip_heredocs(command)):
        if not segment.strip():
            continue
        reason, base_dir = _check_segment(segment.strip(), base_dir)
        if reason:
            return reason
    return None


def check_write(tool_input: dict[str, Any], cwd: str) -> str | None:
    """Deny file-modifying tools aimed at the analysis target."""
    base_dir = cwd or os.getcwd()
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and _path_is_protected(value, base_dir):
            return (
                f"Blocked: '{value}' is inside the analysis target. "
                f"target_engine/ and target_versions/ are read-only copies -- "
                f"never edit them."
            )
    return None


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def evaluate(payload: dict[str, Any]) -> str | None:
    """Return a denial reason for a PreToolUse payload, or None."""
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()

    if tool_name in WRITE_TOOLS:
        return check_write(tool_input, cwd)
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            return check_bash(command, cwd)
    return None


def _deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{reason}\n\nThis is CASCADE-MAP's engine guard "
                    f"(.claude/hooks/guard_engine.py). Do not work around it. "
                    f"Stop and explain what you were trying to do."
                ),
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        reason = evaluate(payload)
    except Exception as exc:  # noqa: BLE001 - a guard must not crash the session
        # Fail closed only where it matters; stay out of the way otherwise.
        if _mentions_protected(raw) or _mentions_venv(raw):
            _deny(
                f"Blocked: the engine guard could not parse this call "
                f"({type(exc).__name__}: {exc}) and it mentions the analysis "
                f"target, so it is denied conservatively."
            )
        return 0
    if reason:
        _deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
