from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .compiler import MindmapCompiler
from .errors import SemanticError
from .models import CompileResult, Diagnostic, RawNode, RelationshipKind, SessionSpec
from .scope import to_string
from .shell import inject_context_into_transition
from .templates import TEMPLATE_RE

_WINDOW_LAYOUTS = {
    "even-horizontal",
    "even-vertical",
    "main-horizontal",
    "main-vertical",
    "tiled",
}


@dataclass(frozen=True)
class NodeInfo:
    node: RawNode
    path: str
    inside_window: bool
    inside_vars: bool


class MapIndex:
    def __init__(self, root: RawNode):
        self.root = root
        self.by_id: dict[str, NodeInfo] = {}
        self._build(root, (), False, False)

    def _build(
        self,
        node: RawNode,
        path: tuple[str, ...],
        inside_window: bool,
        inside_vars: bool,
    ) -> None:
        current_path = (*path, node.text or node.id)
        current_inside_window = inside_window or ("WINDOW" in node.tags)
        current_inside_vars = inside_vars or (node is not self.root and node.text == "vars")
        self.by_id[node.id] = NodeInfo(
            node=node,
            path=" / ".join(current_path),
            inside_window=current_inside_window,
            inside_vars=current_inside_vars,
        )
        for child in node.children:
            self._build(child, current_path, current_inside_window, current_inside_vars)

    def path_for(self, node_id: str | None) -> str | None:
        if node_id is None:
            return None
        info = self.by_id.get(node_id)
        return info.path if info is not None else None


_EXCEPTION_CODE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("UNDEFINED_TEMPLATE_VARIABLE", re.compile(r'undefined template variable "([^"]+)"')),
    ("RELATIONSHIP_TARGET_NOT_FOUND", re.compile(r"unknown relationship target")),
    ("RELATIONSHIP_CYCLE", re.compile(r"relationship cycle detected")),
    ("WINDOW_INHERITANCE_CYCLE", re.compile(r"window inheritance cycle detected")),
    ("DUPLICATE_NODE_ID", re.compile(r"duplicate node id")),
    ("INVALID_LAYOUT", re.compile(r"unsupported .*layout|invalid .*layout", re.IGNORECASE)),
    ("CONFLICTING_NODE_ROLE", re.compile(r"cannot have both PANE and COMMAND tags")),
    ("WRONG_TEMPLATE_TYPE", re.compile(r"Cannot render object .* as a scalar value")),
    ("INVALID_LIST", re.compile(r"Explicit list")),
]


def _code_for_exception(exc: Exception) -> str:
    message = str(exc)
    for code, pattern in _EXCEPTION_CODE_PATTERNS:
        if pattern.search(message):
            return code
    return "SEMANTIC_ERROR"


def _extract_node_id(message: str) -> str | None:
    patterns = [
        re.compile(r"Node ([^ ]+) \""),
        re.compile(r"node '([^']+)'"),
        re.compile(r'node "([^"]+)"'),
        re.compile(r"node ([A-Za-z0-9_:-]+)"),
        re.compile(r"window ([A-Za-z0-9_:-]+)"),
        re.compile(r"detected: ([A-Za-z0-9_:-]+)"),
    ]
    for pattern in patterns:
        match = pattern.search(message)
        if match:
            return match.group(1)
    return None


def _diagnostic_from_exception(exc: Exception, index: MapIndex) -> Diagnostic:
    message = str(exc)
    node_id = _extract_node_id(message)
    return Diagnostic(
        severity="error",
        code=_code_for_exception(exc),
        message=message,
        node_id=node_id,
        node_path=index.path_for(node_id),
    )


def _relationship_kind(
    source: RawNode,
    target: RawNode,
    declared_kind: str | None,
) -> tuple[RelationshipKind | None, Diagnostic | None]:
    normalized = declared_kind.strip().lower() if declared_kind is not None else None
    if normalized:
        if normalized in {"call", "inherit"}:
            return RelationshipKind(normalized), None
        diagnostic = Diagnostic(
            severity="error",
            code="UNKNOWN_RELATIONSHIP_KIND",
            message=(
                f"Relationship from node {source.id!r} to {target.id!r} uses unknown type "
                f"{declared_kind!r}. Expected call or inherit."
            ),
            node_id=source.id,
            relationship_target_id=target.id,
        )
        return None, diagnostic
    inferred = RelationshipKind.INHERIT if "WINDOW" in target.tags else RelationshipKind.CALL
    return inferred, Diagnostic(
        severity="warning",
        code="UNTYPED_RELATIONSHIP",
        message=(
            f'node "{source.text}" used an untyped relationship to "{target.text}"; '
            f"resolved as {inferred.value}"
        ),
        node_id=source.id,
        relationship_target_id=target.id,
    )


def _iter_nodes(root: RawNode) -> list[RawNode]:
    result: list[RawNode] = []

    def walk(node: RawNode) -> None:
        result.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    return result


def _potential_helper(info: NodeInfo) -> bool:
    node = info.node
    if node is None:
        return False
    if info.inside_vars or info.inside_window or "WINDOW" in node.tags or "ALIAS" in node.tags:
        return False
    if node.text in {"vars", "helpers", "functions"} and not node.detail and not node.relationships:
        return False
    if any(key.startswith(("default.", "arg.")) for key in node.attributes):
        return True
    if node.detail and node.detail.strip():
        return True
    if node.relationships:
        return True
    if node.children and any(
        child.detail or child.relationships or child.children for child in node.children
    ):
        return True
    return False


def _is_empty_window(window) -> bool:
    return not window.panes or all(not pane.steps for pane in window.panes)


def _effective_layout_warning(
    root: RawNode, session: SessionSpec, index: MapIndex
) -> list[Diagnostic]:
    windows_by_id = {window.window_id: window for window in session.windows}
    diagnostics: list[Diagnostic] = []
    for node in _iter_nodes(root):
        if "WINDOW" not in node.tags:
            continue
        layout = to_string(node.attributes.get("tmux.layout", "")).strip()
        if not layout:
            continue
        compiled_window = windows_by_id.get(node.id)
        if compiled_window is None:
            continue
        if len(compiled_window.panes) <= 1:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="INEFFECTIVE_LAYOUT",
                    message=(
                        f'window "{compiled_window.name}" defines layout {layout!r} for one pane'
                    ),
                    node_id=node.id,
                    node_path=index.path_for(node.id),
                    field="tmux.layout",
                )
            )
    return diagnostics


def _collect_warnings(root: RawNode, session: SessionSpec, index: MapIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    relationship_target_counts: dict[str, int] = {}

    for node in _iter_nodes(root):
        for relationship in node.relationships:
            target_info = index.by_id.get(relationship.target_id)
            if target_info is None:
                continue
            relationship_target_counts[relationship.target_id] = (
                relationship_target_counts.get(relationship.target_id, 0) + 1
            )
            _kind, warning = _relationship_kind(node, target_info.node, relationship.declared_kind)
            if warning is not None:
                diagnostics.append(
                    warning.model_copy(
                        update={
                            "node_path": index.path_for(node.id),
                        }
                    )
                )

        if "WINDOW" in node.tags:
            for child in node.children:
                explicit_pane = "PANE" in child.tags
                explicit_command = "COMMAND" in child.tags
                if explicit_pane or explicit_command:
                    continue
                if child.children or (child.detail and child.detail.strip()) or child.relationships:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code="INFERRED_PANE",
                            message=(
                                f'node "{child.text}" was inferred as a pane because it has '
                                f"children/detail/relationships"
                            ),
                            node_id=child.id,
                            node_path=index.path_for(child.id),
                        )
                    )

    for window in session.windows:
        if _is_empty_window(window):
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="EMPTY_WINDOW",
                    message=f'window "{window.name}" does not produce any executable pane commands',
                    node_id=window.window_id,
                    node_path=index.path_for(window.window_id),
                )
            )

    diagnostics.extend(_effective_layout_warning(root, session, index))

    for node_id, info in index.by_id.items():
        if _potential_helper(info) and relationship_target_counts.get(node_id, 0) == 0:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="UNUSED_HELPER",
                    message=(
                        f'helper-like node "{info.node.text}" is never referenced by a relationship'
                    ),
                    node_id=node_id,
                    node_path=info.path,
                )
            )

    seen_propagation: set[tuple[str, str]] = set()
    for window in session.windows:
        for pane in window.panes:
            current_env = dict(pane.base_scope.env)
            current_aliases = dict(pane.base_scope.aliases)
            for command in pane.base_scope.pre:
                key = (pane.pane_id, command)
                if key not in seen_propagation and _propagation_skipped(
                    command, current_env, current_aliases
                ):
                    seen_propagation.add(key)
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code="CONTEXT_PROPAGATION_SKIPPED",
                            message=(
                                f"context propagation was skipped for transition command: {command}"
                            ),
                            node_id=window.window_id,
                            node_path=index.path_for(window.window_id),
                        )
                    )
            for step in pane.steps:
                current_env = dict(step.effective_scope.env)
                current_aliases = dict(step.effective_scope.aliases)
                key = (step.node_id, step.command)
                if key not in seen_propagation and _propagation_skipped(
                    step.command, current_env, current_aliases
                ):
                    seen_propagation.add(key)
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code="CONTEXT_PROPAGATION_SKIPPED",
                            message=(
                                "context propagation was skipped for transition "
                                f"command: {step.command}"
                            ),
                            node_id=step.node_id,
                            node_path=index.path_for(step.node_id),
                        )
                    )

    return _dedupe_diagnostics(diagnostics)


def _propagation_skipped(command: str, env: dict[str, str], aliases: dict[str, str]) -> bool:
    if not env and not aliases:
        return False
    stripped = command.lstrip()
    if not (
        stripped == "ssh"
        or stripped.startswith("ssh ")
        or stripped == "sudo"
        or stripped.startswith("sudo ")
    ):
        return False
    rewritten = inject_context_into_transition(command, env, aliases)
    return rewritten == command


def _dedupe_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, str | None, str | None, str]] = set()
    result: list[Diagnostic] = []
    for diagnostic in diagnostics:
        key = (
            diagnostic.code,
            diagnostic.node_id,
            diagnostic.relationship_target_id,
            diagnostic.message,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(diagnostic)
    return result


def compile_with_diagnostics(root: RawNode) -> CompileResult:
    index = MapIndex(root)
    relationship_errors: list[Diagnostic] = []
    for node in _iter_nodes(root):
        layout = to_string(node.attributes.get("tmux.layout", "")).strip()
        if layout and layout not in _WINDOW_LAYOUTS:
            relationship_errors.append(
                Diagnostic(
                    severity="error",
                    code="INVALID_LAYOUT",
                    message=f"window node {node.id!r} uses unsupported tmux.layout {layout!r}",
                    node_id=node.id,
                    node_path=index.path_for(node.id),
                    field="tmux.layout",
                )
            )
        for relationship in node.relationships:
            target_info = index.by_id.get(relationship.target_id)
            if target_info is None:
                relationship_errors.append(
                    Diagnostic(
                        severity="error",
                        code="RELATIONSHIP_TARGET_NOT_FOUND",
                        message=(
                            f"node {node.id!r} references unknown relationship target "
                            f"{relationship.target_id!r}"
                        ),
                        node_id=node.id,
                        node_path=index.path_for(node.id),
                        relationship_target_id=relationship.target_id,
                    )
                )
                continue
            _kind, error_or_warning = _relationship_kind(
                node, target_info.node, relationship.declared_kind
            )
            if error_or_warning is not None and error_or_warning.severity == "error":
                relationship_errors.append(
                    error_or_warning.model_copy(update={"node_path": index.path_for(node.id)})
                )
    if relationship_errors:
        return CompileResult(
            session=None, diagnostics=tuple(_dedupe_diagnostics(relationship_errors))
        )

    try:
        session = MindmapCompiler(root).compile()
    except Exception as exc:
        if isinstance(exc, SemanticError):
            return CompileResult(
                session=None, diagnostics=(_diagnostic_from_exception(exc, index),)
            )
        raise

    diagnostics = _collect_warnings(root, session, index)
    return CompileResult(session=session, diagnostics=tuple(diagnostics))


def build_explain_plan(root: RawNode, session: SessionSpec) -> dict[str, Any]:
    index = MapIndex(root)
    session_plan: dict[str, Any] = {
        "session_name": session.session_name,
        "session_id": session.session_id,
        "start_directory": session.start_directory,
        "windows": [],
        "relationships": [],
        "inheritance": [],
    }

    for node in _iter_nodes(root):
        for relationship in node.relationships:
            target_info = index.by_id.get(relationship.target_id)
            if target_info is None:
                continue
            kind, _warning = _relationship_kind(node, target_info.node, relationship.declared_kind)
            if kind is None:
                continue
            edge = {
                "kind": kind.value,
                "source_node_id": node.id,
                "source_node_path": index.path_for(node.id),
                "target_node_id": relationship.target_id,
                "target_node_path": index.path_for(relationship.target_id),
                "explicit": relationship.declared_kind is not None,
            }
            session_plan["relationships"].append(edge)
            if kind is RelationshipKind.INHERIT:
                session_plan["inheritance"].append(edge)

    for window in session.windows:
        window_info = index.by_id.get(window.window_id)
        window_node = window_info.node if window_info is not None else None
        window_plan: dict[str, Any] = {
            "window_id": window.window_id,
            "name": window.name,
            "layout": window.layout,
            "source_node_path": index.path_for(window.window_id),
            "panes": [],
            "relationships": [
                edge
                for edge in session_plan["relationships"]
                if edge["source_node_id"] == window.window_id
            ],
        }
        if window_node is not None:
            raw_workdir = to_string(window_node.attributes.get("exec.workdir", "")).strip()
            if raw_workdir:
                window_plan["workdir"] = {
                    "value": raw_workdir,
                    "defined_at": f"{window_info.path} [attributes.exec.workdir]",
                }
        for pane in window.panes:
            merged_context_scopes = [
                pane.base_scope,
                *(step.effective_scope for step in pane.steps),
            ]
            context_rows = _merged_scope_context_for_explain(merged_context_scopes, index)
            context_rows.extend(_template_context_rows_for_pane(pane, index))
            deduped_context: dict[str, dict[str, str]] = {row["key"]: row for row in context_rows}
            pane_plan: dict[str, Any] = {
                "pane_id": pane.pane_id,
                "title": pane.title,
                "context": [deduped_context[key] for key in sorted(deduped_context)],
                "pre_commands": [
                    {"command": command, "source": "exec.pre chain"}
                    for command in pane.base_scope.pre
                ],
                "commands": [],
            }
            for step in pane.steps:
                source_field = "detail" if step.payload_source == "detail" else "text"
                pane_plan["commands"].append(
                    {
                        "command": step.command,
                        "display_name": step.display_name,
                        "source_node_id": step.node_id,
                        "source_node_path": index.path_for(step.node_id),
                        "source": f"node {source_field}",
                    }
                )
            window_plan["panes"].append(pane_plan)
        session_plan["windows"].append(window_plan)

    return session_plan


def _merged_scope_context_for_explain(scopes, index: MapIndex) -> list[dict[str, str]]:
    merged_rows: dict[str, dict[str, str]] = {}
    for scope in scopes:
        for key, value in sorted(scope.vars.items()):
            if key.startswith("env.") or key.startswith("session."):
                continue
            defined_at = None
            if key.startswith("vars."):
                defined_at = _vars_defined_at(index.root, key)
            merged_rows[key] = {
                "key": key,
                "value": value,
                **({"defined_at": defined_at} if defined_at else {}),
            }
        for key, value in sorted(scope.env.items()):
            merged_rows[f"env.{key}"] = {
                "key": f"env.{key}",
                "value": value,
                "defined_at": "environment attribute",
            }
    return [merged_rows[key] for key in sorted(merged_rows)]


def _template_context_rows_for_pane(pane, index: MapIndex) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for step in pane.steps:
        info = index.by_id.get(step.node_id)
        if info is None:
            continue
        template = info.node.detail if step.payload_source == "detail" else info.node.text
        if not template:
            continue
        for match in TEMPLATE_RE.finditer(template):
            key = match.group(1).strip()
            if not key or key.startswith("session."):
                continue
            value = step.effective_scope.lookup(key)
            if value is None:
                continue
            if isinstance(value, tuple):
                rendered_value = " ".join(str(item) for item in value)
            else:
                rendered_value = str(value)
            defined_at = None
            if key.startswith("vars."):
                defined_at = _vars_defined_at(index.root, key)
            elif key.startswith("window."):
                defined_at = "window runtime context"
            elif key.startswith("pane."):
                defined_at = "pane runtime context"
            elif key.startswith("node."):
                defined_at = "node runtime context"
            elif key.startswith("env."):
                defined_at = "environment attribute"
            elif key.startswith("args."):
                defined_at = "relationship call arguments"
            rows[key] = {
                "key": key,
                "value": rendered_value,
                **({"defined_at": defined_at} if defined_at else {}),
            }
    return [rows[key] for key in sorted(rows)]


def _vars_defined_at(root: RawNode, path: str) -> str | None:
    parts = path.split(".")
    if not parts or parts[0] != "vars":
        return None
    current = next((child for child in root.children if child.text == "vars"), None)
    if current is None:
        return None
    current_path = [root.text or root.id, current.text or current.id]
    for segment in parts[1:]:
        if segment in current.attributes:
            return " / ".join(current_path) + f" [attribute {segment}]"
        next_node = next((child for child in current.children if child.text == segment), None)
        if next_node is None:
            return None
        current = next_node
        current_path.append(current.text or current.id)
    if current.detail and current.detail.strip():
        return " / ".join(current_path) + " [detail]"
    return " / ".join(current_path)


def explain_text(plan: dict[str, Any]) -> str:
    lines = [f"Session: {plan['session_name']}"]
    if plan.get("start_directory"):
        lines.append(f"Working directory: {plan['start_directory']}")
    lines.append("")

    for window in plan["windows"]:
        lines.append(f"Window: {window['name']}")
        if window.get("source_node_path"):
            lines.append(f"  Source node: {window['source_node_path']}")
        if window.get("layout"):
            lines.append(f"  Layout: {window['layout']}")
        if window.get("workdir"):
            lines.append(f"  Workdir: {window['workdir']['value']}")
            lines.append(f"    defined at: {window['workdir']['defined_at']}")
        lines.append("")
        for pane in window["panes"]:
            lines.append(f"  Pane: {pane.get('title') or pane['pane_id']}")
            lines.append("    Context:")
            for row in pane["context"]:
                lines.append(f"      {row['key']} = {row['value']}")
                if row.get("defined_at"):
                    lines.append(f"        defined at: {row['defined_at']}")
            if pane["pre_commands"]:
                lines.append("    Pre:")
                for index, pre_command in enumerate(pane["pre_commands"], start=1):
                    lines.append(f"      {index}. {pre_command['command']}")
                    lines.append(f"         source: {pre_command['source']}")
            lines.append("    Commands:")
            for index, command in enumerate(pane["commands"], start=1):
                lines.append(f"      {index}. {command['command']}")
                lines.append(f"         source: {command['source']}")
                if command.get("source_node_path"):
                    lines.append(f"         node: {command['source_node_path']}")
            lines.append("")

    if plan["relationships"]:
        lines.append("Relationships:")
        for edge in plan["relationships"]:
            lines.append(f"  kind: {edge['kind']}")
            lines.append(f"    source: {edge['source_node_path']}")
            lines.append(f"    target: {edge['target_node_path']}")
    return "\n".join(lines).rstrip() + "\n"
