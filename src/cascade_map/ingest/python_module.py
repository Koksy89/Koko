"""Parses one already-decoded Python source file with `ast` and mints Elements.

Never imports, execs or evaluates the target. AST only.
"""

from __future__ import annotations

import ast
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

from .constants import BLOB_THRESHOLD_BYTES, DOCSTRING_INLINE_CAP
from .hashing import sha256_hex, sha256_text

_PROPERTY_DECORATOR_SUFFIXES = (".setter", ".getter", ".deleter")


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

    module_docstring = _capped_docstring(tree)
    module_el = Element(
        id=module,
        kind=ElementKind.MODULE,
        name=module.rsplit(".", 1)[-1] if module else module,
        qualname="",
        module=module,
        span=SourceSpan(path=path_str, line=1),
        provenance=_certain_prov(ctx, 1),
        content_hash=sha256_hex(raw_bytes),
        docstring=module_docstring,
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


def _certain_prov(ctx: _Ctx, node: ast.AST | int) -> Provenance:
    return Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN, span=_span(ctx, node))


def _content_hash(ctx: _Ctx, node: ast.AST) -> str:
    segment = ast.get_source_segment(ctx.source, node)
    if segment is None:
        try:
            segment = ast.unparse(node)
        except Exception:
            segment = f"<unavailable:{getattr(node, 'lineno', '?')}>"
    return sha256_text(segment)


def _capped_docstring(node: ast.AST) -> str:
    try:
        ds = ast.get_docstring(node, clean=True)
    except TypeError:
        ds = None
    if not ds:
        return ""
    if len(ds.encode("utf-8", errors="surrogateescape")) > DOCSTRING_INLINE_CAP:
        return ""
    return ds


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
    element = Element(
        id=eid,
        kind=kind,
        name=name,
        qualname=qualname,
        module=ctx.module,
        span=_span(ctx, stmt),
        provenance=_certain_prov(ctx, stmt),
        content_hash=_content_hash(ctx, stmt),
        decorators=decorators,
        signature=_signature_text(stmt),
        docstring=_capped_docstring(stmt),
        parent_id=scope.element_id,
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
    element = Element(
        id=eid,
        kind=ElementKind.CLASS,
        name=name,
        qualname=qualname,
        module=ctx.module,
        span=_span(ctx, stmt),
        provenance=_certain_prov(ctx, stmt),
        content_hash=_content_hash(ctx, stmt),
        decorators=decorators,
        signature=signature,
        docstring=_capped_docstring(stmt),
        parent_id=scope.element_id,
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


def _emit_assignment(ctx: _Ctx, scope: _Scope, name: str, stmt: ast.AST, annotation: ast.AST | None = None) -> None:
    if scope.kind not in ("module", "class"):
        return
    qualname = _child_qualname(scope, name)
    eid = ctx.mint_id(qualname)
    ctx.elements.append(
        Element(
            id=eid,
            kind=ElementKind.ASSIGNMENT,
            name=name,
            qualname=qualname,
            module=ctx.module,
            span=_span(ctx, stmt),
            provenance=_certain_prov(ctx, stmt),
            content_hash=_content_hash(ctx, stmt),
            signature=_safe_unparse(annotation) if annotation is not None else "",
            parent_id=scope.element_id,
        )
    )


def _handle_assign(stmt: ast.Assign, scope: _Scope, ctx: _Ctx) -> None:
    if scope.kind not in ("module", "class"):
        return
    if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and _is_big_constant(stmt.value):
        ctx.pending_blob_spans[id(stmt.value)] = stmt
        return
    for target in stmt.targets:
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
    _emit_assignment(ctx, scope, stmt.target.id, stmt, annotation=stmt.annotation)


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
                    note=f"opaque {literal_kind} literal, never decoded",
                ),
                content_hash=sha256_hex(raw),
                byte_size=len(raw),
            )
        )
