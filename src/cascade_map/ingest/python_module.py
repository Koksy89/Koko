"""Parses one already-decoded Python source file with `ast` and mints Elements.

Never imports, execs or evaluates the target. AST only.
"""

from __future__ import annotations

import ast
import io
import re
import textwrap
import tokenize
from dataclasses import dataclass, field

from cascade_map.contracts.interfaces import (
    Confidence,
    Element,
    ElementKind,
    Method,
    Provenance,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    make_id,
)

from .constants import BLOB_THRESHOLD_BYTES, DOCSTRING_INLINE_CAP, LITERAL_VALUE_CAP_BYTES
from .hashing import sha256_hex, sha256_text

# Token kinds that are pure formatting -- comments and whitespace -- and are
# stripped when building normalized_body_hash. Docstrings are stripped
# separately (see _normalized_body_hash): tokenize has no notion of "this
# string is a docstring", so that part is done with the AST first.
_FORMATTING_TOKENS = frozenset(
    {
        "COMMENT",
        "NL",
        "NEWLINE",
        "INDENT",
        "DEDENT",
        "ENCODING",
        "ENDMARKER",
    }
)

# f-strings are the one construct tokenize spells differently across the
# supported interpreters: 3.11 emits a single STRING token for the whole
# literal, while 3.12+ decomposes it into FSTRING_START / FSTRING_MIDDLE /
# nested tokens / FSTRING_END. Renaming cannot bridge that, so `_norm_tokens`
# detects an f-string opener and collapses the whole literal back to one
# ("STRING", <exact source text>) entry -- which is byte-for-byte what 3.11
# already produces. These names only exist on 3.12+, hence the string
# spellings rather than attribute access.
_FSTRING_START = "FSTRING_START"
_FSTRING_END = "FSTRING_END"

# Field separators for the normalized token stream. Both are control
# characters that cannot appear in Python source outside a string literal,
# and inside one they would be escaped in the source text anyway.
_TOKEN_FIELD_SEP = "\x00"
_TOKEN_RECORD_SEP = "\x01"

_PROPERTY_DECORATOR_SUFFIXES = (".setter", ".getter", ".deleter")

# The same pattern CPython 3.12's `ast._splitlines_no_ff` uses: split on
# \r\n / \n / \r only, keeping the terminator, and ignoring form feed and the
# other characters `str.splitlines` treats as breaks but the parser does not.
# 3.11's hand-rolled loop produces the same list (minus a trailing empty
# element on sources that end with a newline, which is never indexed here),
# so one pattern serves both.
_LINE_PATTERN = re.compile(r"(.*?(?:\r\n|\n|\r|$))")


def _splitlines_no_ff(source: str) -> list[str]:
    """`ast._splitlines_no_ff`, computed once per file instead of once per
    node. See `_source_segment` for why this exists."""
    lines = [m[0] for m in _LINE_PATTERN.finditer(source)]
    if lines and lines[-1] == "":
        # 3.12's regex yields a final zero-width match; 3.11's loop does not.
        # Drop it so the two are literally the same list.
        lines.pop()
    return lines


def _slice_line(line: str, start: int, stop: int | None) -> str:
    """`col_offset`/`end_col_offset` are UTF-8 **byte** offsets, so the
    stdlib slices `line.encode()` and decodes back. For an all-ASCII line
    byte offsets and character offsets coincide exactly, so the direct slice
    is identical and skips two transcodes -- which is most lines of most
    files."""
    if line.isascii():
        return line[start:stop]
    return line.encode()[start:stop].decode()


def _source_segment(ctx: _Ctx, node: ast.AST) -> str | None:
    """`ast.get_source_segment(source, node, padded=False)` with the line
    split hoisted out.

    The stdlib re-splits the **entire** source file on every call, making
    inventory O(file_length x elements_in_file). On the shape this tool is
    built for -- one very large module -- that dominated the whole run
    (measured: 202.65s -> 2.86s on a 1.3 MB single file). The body below is
    the stdlib's own body verbatim after its split, so agreement is by
    construction, not by luck; `tests/test_ingest_source_segment.py` asserts
    it against `ast.get_source_segment` over a corpus including non-ASCII and
    multi-line nodes.

    Only `padded=False` is implemented -- the one form this module uses."""
    try:
        if node.end_lineno is None or node.end_col_offset is None:  # type: ignore[attr-defined]
            return None
        lineno = node.lineno - 1  # type: ignore[attr-defined]
        end_lineno = node.end_lineno - 1  # type: ignore[attr-defined]
        col_offset = node.col_offset  # type: ignore[attr-defined]
        end_col_offset = node.end_col_offset  # type: ignore[attr-defined]
    except AttributeError:
        return None

    lines = ctx.source_lines()
    if end_lineno == lineno:
        return _slice_line(lines[lineno], col_offset, end_col_offset)

    first = _slice_line(lines[lineno], col_offset, None)
    last = _slice_line(lines[end_lineno], 0, end_col_offset)
    return "".join([first, *lines[lineno + 1 : end_lineno], last])


@dataclass
class _Scope:
    qualname: str
    element_id: str
    kind: str  # "module" | "class" | "function"


@dataclass
class _Ctx:
    module: str
    path: str
    source: str
    counts: dict[str, int] = field(default_factory=dict)
    blob_count: int = 0
    elements: list[Element] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    pending_blob_spans: dict[int, ast.AST] = field(default_factory=dict)
    # Lazily split once per file and reused by every `_source_segment` call.
    lines: list[str] | None = field(default=None, repr=False)

    def source_lines(self) -> list[str]:
        if self.lines is None:
            self.lines = _splitlines_no_ff(self.source)
        return self.lines

    def next_ordinal(self, qualname: str) -> int:
        n = self.counts.get(qualname, 0) + 1
        self.counts[qualname] = n
        return n

    def mint_id(self, qualname: str) -> str:
        return make_id(self.module, qualname, self.next_ordinal(qualname))


def parse_python_file(
    module: str,
    path_str: str,
    source: str,
    raw_bytes: bytes,
    tree: ast.Module,
    local_top_names: frozenset[str],
) -> tuple[list[Element], list[Unresolved]]:
    ctx = _Ctx(module=module, path=path_str, source=source)

    module_docstring, module_docstring_note = _capped_docstring(tree)
    module_el = Element(
        id=module,
        kind=ElementKind.MODULE,
        name=module.rsplit(".", 1)[-1] if module else module,
        qualname="",
        module=module,
        span=SourceSpan(path=path_str, line=1),
        provenance=_certain_prov(ctx, 1, note=module_docstring_note),
        content_hash=sha256_hex(raw_bytes),
        docstring=module_docstring,
        normalized_body_hash=_normalized_body_hash(ctx, tree),
    )
    ctx.elements.append(module_el)

    root_scope = _Scope(qualname="", element_id="", kind="module")
    _visit_body(tree.body, root_scope, ctx, local_top_names)

    _emit_blobs(ctx, tree)

    return ctx.elements, ctx.unresolved


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _span(ctx: _Ctx, node: ast.AST | int) -> SourceSpan:
    if isinstance(node, int):
        return SourceSpan(path=ctx.path, line=node)
    return SourceSpan(
        path=ctx.path,
        line=getattr(node, "lineno", 1),
        end_line=getattr(node, "end_lineno", None),
        col=getattr(node, "col_offset", None),
    )


def _certain_prov(ctx: _Ctx, node: ast.AST | int, note: str = "") -> Provenance:
    return Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN, span=_span(ctx, node), note=note)


def _content_hash(ctx: _Ctx, node: ast.AST) -> str:
    segment = _source_segment(ctx, node)
    if segment is None:
        try:
            segment = ast.unparse(node)
        except Exception:
            segment = f"<unavailable:{getattr(node, 'lineno', '?')}>"
    return sha256_text(segment)


def _norm_tokens(segment: str) -> str:
    """Interpreter-independent normalized token stream for `segment`.

    The representation is `tokenize.tok_name[tok.type]` (the token's *name*)
    paired with `tok.string`, joined with control-character separators --
    never `tok.type` itself. Token type numbers are CPython-internal and were
    renumbered between 3.11 and 3.12, so hashing them made the "is this the
    same logic" hash change for every element merely by changing interpreter.
    Names are part of the documented `tokenize` surface and are identical on
    3.11, 3.12 and 3.13 for every token this stream keeps.

    One construct needs more than renaming: 3.11 emits a single STRING token
    for an f-string, 3.12+ emits FSTRING_START / FSTRING_MIDDLE / the
    replacement-field tokens / FSTRING_END. That is a structural difference,
    so an f-string is re-collapsed here into one ("STRING", exact source
    text) record by slicing `segment` from the opener's start position to the
    closer's end position, tracking nesting so that an f-string inside an
    f-string's replacement field is absorbed into the outer one. The result
    is exactly the record 3.11 produces unaided.

    Known limit: a segment using 3.12-only f-string syntax (same quote reused
    inside a replacement field, or a backslash in one) cannot be tokenized at
    all by 3.11, so no representation can make those two interpreters agree;
    3.11 raises and the caller falls back to hashing the segment text, which
    is itself version-stable. Every f-string that 3.11 can tokenize hashes
    identically on all three versions.
    """
    lines = segment.splitlines(keepends=True)

    def _cut(start: tuple[int, int], end: tuple[int, int]) -> str:
        srow, scol = start
        erow, ecol = end
        if srow == erow:
            return lines[srow - 1][scol:ecol]
        return "".join([lines[srow - 1][scol:], *lines[srow : erow - 1], lines[erow - 1][:ecol]])

    records: list[str] = []
    tokens = tokenize.generate_tokens(io.StringIO(segment).readline)
    for tok in tokens:
        name = tokenize.tok_name[tok.type]
        if name == _FSTRING_START:
            depth = 1
            start = tok.start
            end = tok.end
            for inner in tokens:
                inner_name = tokenize.tok_name[inner.type]
                if inner_name == _FSTRING_START:
                    depth += 1
                elif inner_name == _FSTRING_END:
                    depth -= 1
                    if depth == 0:
                        end = inner.end
                        break
            records.append("STRING" + _TOKEN_FIELD_SEP + _cut(start, end))
            continue
        if name in _FORMATTING_TOKENS:
            continue
        records.append(name + _TOKEN_FIELD_SEP + tok.string)
    return _TOKEN_RECORD_SEP.join(records)


def _normalized_body_hash(ctx: _Ctx, node: ast.AST) -> str:
    """Hash of `node`'s body with comments, docstrings and whitespace
    normalized away -- "is this the same logic", distinct from
    `content_hash`'s "is this the same bytes". Uses `tokenize` only, never
    `ast.parse`/`compile` on target source (constraint 1 applies to every
    reparse, not just the first one).

    The hashed representation is interpreter-independent by construction; see
    `_norm_tokens`. This value is compared across runs and across engine
    versions (cards 6 and 18), so a value that shifts with the interpreter
    would report an entire engine as rewritten.

    Deliberately excludes the `def name(...):`/`class Name(...):` header:
    two identically-bodied functions with different names or signatures are
    exactly the DUPLICATED_LOGIC case card 5 needs this for, and a header
    difference must not hide that. Built from each body statement's own
    source segment (skipping a leading docstring statement), not the node's
    whole segment.

    Empty string for elements with no body (anything but MODULE/CLASS/
    FUNCTION/METHOD/PROPERTY), or an empty body."""
    body = getattr(node, "body", None)
    if not body:
        return ""

    statements = body
    first = statements[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        statements = statements[1:]
    if not statements:
        return ""

    segments = [_source_segment(ctx, s) for s in statements]
    segments = [s for s in segments if s is not None]
    if not segments:
        return ""
    segment = textwrap.dedent("\n".join(segments))

    try:
        normalized = _norm_tokens(segment)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # Tokenizing an extracted segment in isolation can occasionally fail
        # on code that only parses in its original context (e.g. a `match`
        # soft keyword edge case), or on 3.12-only f-string syntax under
        # 3.11. Fall back to the docstring-stripped segment itself rather
        # than losing the fact entirely.
        return sha256_text(segment)
    return sha256_text(normalized)


def _literal_value_and_note(value_node: ast.AST | None) -> tuple[str, str]:
    """`repr()` of an assignment's value when it is a literal constant --
    empty when it is not, which is the common case and must never read as
    "the literal was empty": `repr()` of any real literal (including `""`,
    `0`, `False`, `None`) is always a non-empty string, so the empty default
    is unambiguous on its own. A note is still attached when a str/bytes
    literal is capped, the same way docstring capping is surfaced.

    Never a path for blob content: str/bytes literals at or above
    BLOB_THRESHOLD_BYTES are already absorbed into a BLOB element before this
    runs (see `_handle_assign`/`_handle_annassign`), so this function's own
    LITERAL_VALUE_CAP_BYTES check is a defensive second gate, not the
    primary one.
    """
    node = value_node
    sign = ""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        if isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float, complex)):
            sign = "-" if isinstance(node.op, ast.USub) else "+"
            node = node.operand
    if not isinstance(node, ast.Constant):
        return "", ""
    value = node.value
    if isinstance(value, (str, bytes)):
        size = len(value.encode("utf-8", errors="surrogateescape")) if isinstance(value, str) else len(value)
        if size >= LITERAL_VALUE_CAP_BYTES:
            return "", (
                f"literal_value omitted: {size} bytes >= LITERAL_VALUE_CAP_BYTES="
                f"{LITERAL_VALUE_CAP_BYTES} (never a path for blob content)"
            )
    text = repr(value)
    return (f"-{text}" if sign == "-" else text), ""


def _capped_docstring(node: ast.AST) -> tuple[str, str]:
    """The docstring, capped, plus a note to attach to the element's own
    Provenance whenever the cap actually did something -- a threshold that
    silently shapes an emitted record is invisible to whoever has to trust
    it, so the omission itself becomes part of the record."""
    try:
        ds = ast.get_docstring(node, clean=True)
    except TypeError:
        ds = None
    if not ds:
        return "", ""
    size = len(ds.encode("utf-8", errors="surrogateescape"))
    if size > DOCSTRING_INLINE_CAP:
        return "", (
            f"docstring omitted: {size} bytes exceeds DOCSTRING_INLINE_CAP="
            f"{DOCSTRING_INLINE_CAP}; the underlying literal is still visited "
            "by blob detection separately"
        )
    return ds, ""


def _blob_threshold_note(value_node: ast.AST | None) -> str:
    """A note for elements whose value is a string/bytes literal that was
    evaluated against BLOB_THRESHOLD_BYTES but did *not* clear it -- so the
    threshold's effect (or non-effect) is visible on the record either way,
    not only when it fires."""
    if not isinstance(value_node, ast.Constant):
        return ""
    value = value_node.value
    if isinstance(value, str):
        size = len(value.encode("utf-8", errors="surrogateescape"))
    elif isinstance(value, bytes):
        size = len(value)
    else:
        return ""
    if size >= BLOB_THRESHOLD_BYTES:
        return ""  # absorbed into a BLOB element instead -- see _emit_blobs
    return (
        f"literal value is {size} bytes (< BLOB_THRESHOLD_BYTES="
        f"{BLOB_THRESHOLD_BYTES}; not recorded as an opaque BLOB)"
    )


def _child_qualname(scope: _Scope, name: str) -> str:
    if scope.kind == "module":
        return name
    if scope.kind == "class":
        return f"{scope.qualname}.{name}"
    return f"{scope.qualname}.<locals>.{name}"


def _is_big_constant(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="surrogateescape")) >= BLOB_THRESHOLD_BYTES
    if isinstance(value, bytes):
        return len(value) >= BLOB_THRESHOLD_BYTES
    return False


def _blob_bytes(node: ast.Constant) -> bytes:
    value = node.value
    return value.encode("utf-8", errors="surrogateescape") if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# traversal
# ---------------------------------------------------------------------------

_CONTROL_FLOW_NO_SCOPE = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)


def _visit_body(stmts: list[ast.stmt], scope: _Scope, ctx: _Ctx, local_top_names: frozenset[str]) -> None:
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _handle_def(stmt, scope, ctx, local_top_names)
        elif isinstance(stmt, ast.ClassDef):
            _handle_class(stmt, scope, ctx, local_top_names)
        elif isinstance(stmt, ast.Assign):
            _handle_assign(stmt, scope, ctx)
        elif isinstance(stmt, ast.AnnAssign):
            _handle_annassign(stmt, scope, ctx)
        elif isinstance(stmt, ast.AugAssign):
            _handle_augassign(stmt, scope, ctx)
        elif isinstance(stmt, ast.Import):
            _handle_import(stmt, scope, ctx, local_top_names)
        elif isinstance(stmt, ast.ImportFrom):
            _handle_import_from(stmt, scope, ctx, local_top_names)
        elif isinstance(stmt, _CONTROL_FLOW_NO_SCOPE):
            _visit_body(stmt.body, scope, ctx, local_top_names)
            orelse = getattr(stmt, "orelse", None)
            if orelse:
                _visit_body(orelse, scope, ctx, local_top_names)
        elif isinstance(stmt, ast.Try):
            _visit_body(stmt.body, scope, ctx, local_top_names)
            for handler in stmt.handlers:
                _visit_body(handler.body, scope, ctx, local_top_names)
            if stmt.orelse:
                _visit_body(stmt.orelse, scope, ctx, local_top_names)
            if stmt.finalbody:
                _visit_body(stmt.finalbody, scope, ctx, local_top_names)
        elif hasattr(ast, "Match") and isinstance(stmt, getattr(ast, "Match")):
            for case in stmt.cases:
                _visit_body(case.body, scope, ctx, local_top_names)
        # everything else (Expr, Return, Pass, Raise, ...) carries no element


def _classify_function_kind(stmt: ast.AST, scope: _Scope) -> ElementKind:
    if scope.kind != "class":
        return ElementKind.FUNCTION
    for dec in stmt.decorator_list:
        try:
            text = ast.unparse(dec)
        except Exception:
            continue
        if text == "property":
            return ElementKind.PROPERTY
        if any(text.endswith(suffix) for suffix in _PROPERTY_DECORATOR_SUFFIXES):
            return ElementKind.PROPERTY
    return ElementKind.METHOD


def _signature_text(stmt: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args_text = ast.unparse(stmt.args)
    except Exception:
        args_text = ""
    ret = ""
    if stmt.returns is not None:
        try:
            ret = f" -> {ast.unparse(stmt.returns)}"
        except Exception:
            ret = ""
    return f"({args_text}){ret}"


def _iter_params(args: ast.arguments) -> list[ast.arg]:
    result: list[ast.arg] = []
    result.extend(args.posonlyargs)
    result.extend(args.args)
    if args.vararg is not None:
        result.append(args.vararg)
    result.extend(args.kwonlyargs)
    if args.kwarg is not None:
        result.append(args.kwarg)
    return result


def _handle_def(stmt, scope: _Scope, ctx: _Ctx, local_top_names: frozenset[str]) -> None:
    name = stmt.name
    qualname = _child_qualname(scope, name)
    kind = _classify_function_kind(stmt, scope)
    decorators = tuple(_safe_unparse(d) for d in stmt.decorator_list)
    eid = ctx.mint_id(qualname)
    docstring, docstring_note = _capped_docstring(stmt)
    element = Element(
        id=eid,
        kind=kind,
        name=name,
        qualname=qualname,
        module=ctx.module,
        span=_span(ctx, stmt),
        provenance=_certain_prov(ctx, stmt, note=docstring_note),
        content_hash=_content_hash(ctx, stmt),
        decorators=decorators,
        signature=_signature_text(stmt),
        docstring=docstring,
        parent_id=scope.element_id,
        normalized_body_hash=_normalized_body_hash(ctx, stmt),
    )
    ctx.elements.append(element)

    for arg in _iter_params(stmt.args):
        pqual = f"{qualname}.<param>.{arg.arg}"
        peid = ctx.mint_id(pqual)
        ctx.elements.append(
            Element(
                id=peid,
                kind=ElementKind.PARAMETER,
                name=arg.arg,
                qualname=pqual,
                module=ctx.module,
                span=_span(ctx, arg),
                provenance=_certain_prov(ctx, arg),
                content_hash=_content_hash(ctx, arg),
                signature=_safe_unparse(arg.annotation) if arg.annotation is not None else "",
                parent_id=eid,
            )
        )

    new_scope = _Scope(qualname=qualname, element_id=eid, kind="function")
    _visit_body(stmt.body, new_scope, ctx, local_top_names)


def _handle_class(stmt: ast.ClassDef, scope: _Scope, ctx: _Ctx, local_top_names: frozenset[str]) -> None:
    name = stmt.name
    qualname = _child_qualname(scope, name)
    eid = ctx.mint_id(qualname)
    bases = [_safe_unparse(b) for b in stmt.bases]
    kwargs = [f"{kw.arg}={_safe_unparse(kw.value)}" for kw in stmt.keywords]
    signature = f"({', '.join(bases + kwargs)})" if (bases or kwargs) else ""
    decorators = tuple(_safe_unparse(d) for d in stmt.decorator_list)
    docstring, docstring_note = _capped_docstring(stmt)
    element = Element(
        id=eid,
        kind=ElementKind.CLASS,
        name=name,
        qualname=qualname,
        module=ctx.module,
        span=_span(ctx, stmt),
        provenance=_certain_prov(ctx, stmt, note=docstring_note),
        content_hash=_content_hash(ctx, stmt),
        decorators=decorators,
        signature=signature,
        docstring=docstring,
        parent_id=scope.element_id,
        normalized_body_hash=_normalized_body_hash(ctx, stmt),
    )
    ctx.elements.append(element)
    new_scope = _Scope(qualname=qualname, element_id=eid, kind="class")
    _visit_body(stmt.body, new_scope, ctx, local_top_names)


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _collect_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _collect_target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_collect_target_names(elt))
        return names
    return []


def _emit_assignment(
    ctx: _Ctx,
    scope: _Scope,
    name: str,
    stmt: ast.AST,
    annotation: ast.AST | None = None,
    value_node: ast.AST | None = None,
) -> None:
    """`value_node` is the expression `name` is *directly* bound to -- only
    set by the caller when that binding is unambiguous (a plain `Name`
    target, not one element of a tuple-unpacking target), since
    `literal_value` must never guess which side of an unpacking a literal
    belongs to."""
    if scope.kind not in ("module", "class"):
        return
    qualname = _child_qualname(scope, name)
    eid = ctx.mint_id(qualname)
    blob_note = _blob_threshold_note(getattr(stmt, "value", None))
    literal_value, literal_note = _literal_value_and_note(value_node)
    note = "; ".join(part for part in (blob_note, literal_note) if part)
    ctx.elements.append(
        Element(
            id=eid,
            kind=ElementKind.ASSIGNMENT,
            name=name,
            qualname=qualname,
            module=ctx.module,
            span=_span(ctx, stmt),
            provenance=_certain_prov(ctx, stmt, note=note),
            content_hash=_content_hash(ctx, stmt),
            signature=_safe_unparse(annotation) if annotation is not None else "",
            parent_id=scope.element_id,
            literal_value=literal_value,
        )
    )


def _handle_assign(stmt: ast.Assign, scope: _Scope, ctx: _Ctx) -> None:
    if scope.kind not in ("module", "class"):
        return
    if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and _is_big_constant(stmt.value):
        ctx.pending_blob_spans[id(stmt.value)] = stmt
        return
    for target in stmt.targets:
        if isinstance(target, ast.Name):
            _emit_assignment(ctx, scope, target.id, stmt, value_node=stmt.value)
        else:
            for name in _collect_target_names(target):
                _emit_assignment(ctx, scope, name, stmt)


def _handle_annassign(stmt: ast.AnnAssign, scope: _Scope, ctx: _Ctx) -> None:
    if scope.kind not in ("module", "class"):
        return
    if not isinstance(stmt.target, ast.Name):
        return
    if stmt.value is not None and _is_big_constant(stmt.value):
        ctx.pending_blob_spans[id(stmt.value)] = stmt
        return
    _emit_assignment(ctx, scope, stmt.target.id, stmt, annotation=stmt.annotation, value_node=stmt.value)


def _handle_augassign(stmt: ast.AugAssign, scope: _Scope, ctx: _Ctx) -> None:
    if scope.kind not in ("module", "class"):
        return
    if not isinstance(stmt.target, ast.Name):
        return
    _emit_assignment(ctx, scope, stmt.target.id, stmt)


def _handle_import(stmt: ast.Import, scope: _Scope, ctx: _Ctx, local_top_names: frozenset[str]) -> None:
    for alias in stmt.names:
        dotted = alias.name
        bound = alias.asname or dotted.split(".")[0]
        qualname = _child_qualname(scope, bound)
        eid = ctx.mint_id(qualname)
        signature = f"import {dotted}" + (f" as {alias.asname}" if alias.asname else "")
        ctx.elements.append(
            Element(
                id=eid,
                kind=ElementKind.IMPORT,
                name=bound,
                qualname=qualname,
                module=ctx.module,
                span=_span(ctx, stmt),
                provenance=_certain_prov(ctx, stmt),
                content_hash=_content_hash(ctx, stmt),
                signature=signature,
                parent_id=scope.element_id,
            )
        )
        top = dotted.split(".")[0]
        if top not in local_top_names:
            unresolved_id = make_id(ctx.module, bound)
            ctx.unresolved.append(
                Unresolved(
                    id=unresolved_id,
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=_span(ctx, stmt),
                    description=f"Import '{dotted}' not found",
                    attempted=(Method.IMPORT_ABSOLUTE,),
                )
            )


def _handle_import_from(stmt: ast.ImportFrom, scope: _Scope, ctx: _Ctx, local_top_names: frozenset[str]) -> None:
    module_name = stmt.module or ""
    level = stmt.level
    for alias in stmt.names:
        if alias.name == "*":
            continue
        bound = alias.asname or alias.name
        qualname = _child_qualname(scope, bound)
        eid = ctx.mint_id(qualname)
        prefix = "." * level
        signature = f"from {prefix}{module_name} import {alias.name}" + (
            f" as {alias.asname}" if alias.asname else ""
        )
        ctx.elements.append(
            Element(
                id=eid,
                kind=ElementKind.IMPORT,
                name=bound,
                qualname=qualname,
                module=ctx.module,
                span=_span(ctx, stmt),
                provenance=_certain_prov(ctx, stmt),
                content_hash=_content_hash(ctx, stmt),
                signature=signature,
                parent_id=scope.element_id,
            )
        )
        if level == 0 and module_name:
            top = module_name.split(".")[0]
            if top not in local_top_names:
                unresolved_id = make_id(ctx.module, bound)
                ctx.unresolved.append(
                    Unresolved(
                        id=unresolved_id,
                        reason=UnresolvedReason.MISSING_TARGET,
                        span=_span(ctx, stmt),
                        description=f"Import '{alias.name}' not found (from {module_name})",
                        attempted=(Method.IMPORT_ABSOLUTE,),
                    )
                )


def _emit_blobs(ctx: _Ctx, tree: ast.Module) -> None:
    candidates = [node for node in ast.walk(tree) if _is_big_constant(node)]
    candidates.sort(key=lambda n: (n.lineno, n.col_offset))
    for node in candidates:
        ctx.blob_count += 1
        qualname = f"@blob#{ctx.blob_count}"
        eid = make_id(ctx.module, qualname, 1)
        span_node = ctx.pending_blob_spans.get(id(node), node)
        raw = _blob_bytes(node)
        literal_kind = "bytes" if isinstance(node.value, bytes) else "str"
        ctx.elements.append(
            Element(
                id=eid,
                kind=ElementKind.BLOB,
                name=qualname,
                qualname=qualname,
                module=ctx.module,
                span=_span(ctx, span_node),
                provenance=Provenance(
                    method=Method.AST_DIRECT,
                    confidence=Confidence.CERTAIN,
                    span=_span(ctx, span_node),
                    note=(
                        f"opaque {literal_kind} literal, never decoded; "
                        f"{len(raw)} bytes >= BLOB_THRESHOLD_BYTES={BLOB_THRESHOLD_BYTES}"
                    ),
                ),
                content_hash=sha256_hex(raw),
                byte_size=len(raw),
            )
        )
