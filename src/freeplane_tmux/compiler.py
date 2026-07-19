from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypeAlias

from .errors import SemanticError
from .models import (
    AliasTemplate,
    CommandStep,
    PaneSpec,
    RawNode,
    ScopeLayer,
    SessionSpec,
    WindowSpec,
)
from .scope import ScopeResolver, combine_layer, split_attributes, to_string
from .text import split_shell_commands

ALIAS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class _WindowLocation:
    node: RawNode
    path: tuple[RawNode, ...]


@dataclass(frozen=True)
class _ImplicitPanePlan:
    command_nodes: tuple[RawNode, ...]
    ordinal: int
    expands_window_root: bool = False


@dataclass(frozen=True)
class _ExplicitPanePlan:
    pane_root: RawNode


_PanePlan: TypeAlias = _ImplicitPanePlan | _ExplicitPanePlan


@dataclass(frozen=True)
class _WindowPlan:
    mode: Literal["single_implicit_pane", "pane_list", "mixed"]
    panes: tuple[_PanePlan, ...]


class MindmapCompiler:
    """Normalize a raw Freeplane tree into an execution-oriented session plan."""

    def __init__(self, root: RawNode):
        self.root = root
        self.index: dict[str, RawNode] = {}
        self._resolver = ScopeResolver()
        self._build_index(root)
        self._validate_relationship_targets()

    def compile(self) -> SessionSpec:
        root_layers = (self._node_layer(self.root),)
        session_name = self._render_node_text(
            self.root.text,
            root_layers,
            builtins={},
            subject=f"session name from node {self.root.id!r}",
            default="freeplane",
        )
        start_directory = self._compile_session_start_directory(root_layers, session_name)
        windows = tuple(
            self._compile_window(location, session_name) for location in self._collect_windows()
        )
        return SessionSpec(
            session_id=self.root.id,
            session_name=session_name,
            start_directory=start_directory,
            windows=windows,
        )

    def _compile_session_start_directory(
        self,
        root_layers: tuple[ScopeLayer, ...],
        session_name: str,
    ) -> str | None:
        raw_workdir = to_string(self.root.attributes.get("workdir", ""))
        if not raw_workdir.strip():
            return None
        rendered = self._render_node_text(
            raw_workdir,
            root_layers,
            builtins={"session-name": session_name},
            subject=f"session workdir from root node {self.root.id!r}",
        )
        return rendered or None

    def _build_index(self, node: RawNode) -> None:
        if node.id in self.index:
            raise SemanticError(f"duplicate node id: {node.id}")
        self.index[node.id] = node
        for child in node.children:
            self._build_index(child)

    def _validate_relationship_targets(self) -> None:
        for node in self.index.values():
            for target_id in self._relationship_target_ids(node):
                if target_id not in self.index:
                    raise SemanticError(
                        f"node {node.id!r} references unknown relationship target {target_id!r}"
                    )

    def _collect_windows(self) -> tuple[_WindowLocation, ...]:
        found: list[_WindowLocation] = []

        def walk(node: RawNode, path: tuple[RawNode, ...], inside_window: bool) -> None:
            current_path = (*path, node)
            is_window = "WINDOW" in node.tags
            if is_window and not inside_window:
                found.append(_WindowLocation(node=node, path=current_path))
                inside_window = True
            for child in self._non_alias_children(node):
                walk(child, current_path, inside_window)

        walk(self.root, (), False)
        return tuple(found)

    @staticmethod
    def _is_alias(node: RawNode) -> bool:
        return "ALIAS" in node.tags

    def _non_alias_children(self, node: RawNode) -> list[RawNode]:
        return [child for child in node.children if not self._is_alias(child)]

    @staticmethod
    def _relationship_target_ids(node: RawNode) -> tuple[str, ...]:
        return tuple(relationship.target_id for relationship in node.relationships)

    def _relationship_targets(self, node: RawNode) -> tuple[RawNode, ...]:
        return tuple(self.index[target_id] for target_id in self._relationship_target_ids(node))

    def _node_layer(self, node: RawNode) -> ScopeLayer:
        attributes = split_attributes(node.attributes)
        aliases = self._aliases_declared_by(node)
        return combine_layer(attributes, aliases=aliases)

    def _render_node_text(
        self,
        template: str,
        layers: tuple[ScopeLayer, ...],
        *,
        builtins: dict[str, str],
        subject: str,
        default: str = "",
    ) -> str:
        if not template.strip():
            return default
        scope = self._resolver.resolve(
            layers,
            builtins=builtins,
            strict=False,
            subject=subject,
        )
        rendered = self._resolver.render_value(template, scope, subject=subject).strip()
        return rendered or default

    def _aliases_declared_by(self, node: RawNode) -> dict[str, AliasTemplate]:
        aliases: dict[str, AliasTemplate] = {}
        for child in node.children:
            if not self._is_alias(child):
                continue
            name = child.text.strip()
            if not ALIAS_NAME_RE.fullmatch(name):
                raise SemanticError(
                    f"invalid alias name {name!r} in node {child.id!r}; "
                    "use letters, digits, underscore, or hyphen"
                )
            commands = tuple(self._compile_alias_body(child, relationship_stack=()))
            if not commands:
                raise SemanticError(f"alias {name!r} in node {child.id!r} has an empty body")
            aliases[name] = AliasTemplate(
                name=name,
                command_templates=commands,
                source_node_id=child.id,
            )
        return aliases

    def _compile_alias_body(
        self,
        alias_node: RawNode,
        *,
        relationship_stack: tuple[str, ...],
    ) -> list[str]:
        commands: list[str] = []
        targets = self._relationship_targets(alias_node)

        if alias_node.detail and alias_node.detail.strip():
            commands.extend(split_shell_commands(alias_node.detail))
        elif targets:
            for target in targets:
                commands.extend(
                    self._compile_function_template(
                        target,
                        relationship_stack=relationship_stack,
                    )
                )
        elif alias_node.text.strip():
            commands.extend(split_shell_commands(alias_node.text))

        for child in self._non_alias_children(alias_node):
            commands.extend(
                self._compile_function_template(child, relationship_stack=relationship_stack)
            )
        return commands

    def _compile_function_template(
        self,
        node: RawNode,
        *,
        relationship_stack: tuple[str, ...],
        function_root: bool = True,
    ) -> list[str]:
        if node.id in relationship_stack:
            cycle = " -> ".join([*relationship_stack, node.id])
            raise SemanticError(f"relationship cycle detected: {cycle}")

        next_stack = (*relationship_stack, node.id)
        children = self._non_alias_children(node)
        targets = self._relationship_targets(node)
        commands: list[str] = []

        if node.detail and node.detail.strip():
            commands.extend(split_shell_commands(node.detail))
        elif node.text.strip() and (not function_root or not children):
            commands.extend(split_shell_commands(node.text))

        for target in targets:
            commands.extend(
                self._compile_function_template(
                    target,
                    relationship_stack=next_stack,
                    function_root=True,
                )
            )

        for child in children:
            commands.extend(
                self._compile_function_template(
                    child,
                    relationship_stack=next_stack,
                    function_root=False,
                )
            )
        return commands

    @staticmethod
    def _window_child_role(node: RawNode) -> Literal["command", "pane"]:
        """Classify one direct WINDOW child using the map grammar.

        Direct WINDOW children are not interpreted through a global window heuristic.
        Each child has one local role:

        * a plain leaf is a command in an implicit pane;
        * a branch, detailed invocation, relationship invocation, or PANE-tagged node
          declares its own pane;
        * COMMAND/PANE tags are explicit overrides for otherwise ambiguous nodes.
        """

        explicit_pane = "PANE" in node.tags
        explicit_command = "COMMAND" in node.tags
        if explicit_pane and explicit_command:
            raise SemanticError(f"node {node.id!r} cannot have both PANE and COMMAND tags")
        if explicit_pane:
            return "pane"
        if explicit_command:
            return "command"
        if node.children or (node.detail and node.detail.strip()) or node.relationships:
            return "pane"
        return "command"

    def _plan_window(self, window: RawNode) -> _WindowPlan:
        attributes = {key: to_string(value) for key, value in window.attributes.items()}
        explicit = attributes.get("window-mode") or attributes.get("window_mode")
        children = tuple(self._non_alias_children(window))

        # A command attached to the WINDOW node owns one implicit pane and expands
        # its descendants in that same execution context.
        if window.detail or window.relationships:
            if explicit in {"pane-list", "pane_list"} and children:
                raise SemanticError(
                    f"window {window.id!r} combines a root command/relationship with "
                    "explicit pane-list mode; use single-pane or move the command into a pane"
                )
            return _WindowPlan(
                mode="single_implicit_pane",
                panes=(
                    _ImplicitPanePlan(
                        command_nodes=(),
                        ordinal=0,
                        expands_window_root=True,
                    ),
                ),
            )

        if explicit in {"single-pane", "single_implicit_pane"}:
            return _WindowPlan(
                mode="single_implicit_pane",
                panes=(_ImplicitPanePlan(command_nodes=children, ordinal=0),),
            )

        if explicit in {"pane-list", "pane_list"}:
            return _WindowPlan(
                mode="pane_list",
                panes=tuple(_ExplicitPanePlan(child) for child in children),
            )

        if explicit:
            raise SemanticError(f"unsupported window mode {explicit!r} in window {window.id!r}")

        # Default WINDOW grammar is a sequence. Consecutive command entries share
        # one implicit pane; pane declarations retain their position. This preserves
        # source order and supports mixed windows without switching the entire window
        # between two inferred modes.
        panes: list[_PanePlan] = []
        pending_commands: list[RawNode] = []
        implicit_ordinal = 0

        def flush_commands() -> None:
            nonlocal implicit_ordinal
            if not pending_commands:
                return
            panes.append(
                _ImplicitPanePlan(
                    command_nodes=tuple(pending_commands),
                    ordinal=implicit_ordinal,
                )
            )
            implicit_ordinal += 1
            pending_commands.clear()

        for child in children:
            if self._window_child_role(child) == "command":
                pending_commands.append(child)
                continue
            flush_commands()
            panes.append(_ExplicitPanePlan(child))
        flush_commands()

        if not panes:
            return _WindowPlan(mode="pane_list", panes=())
        if len(panes) == 1 and isinstance(panes[0], _ImplicitPanePlan):
            mode: Literal["single_implicit_pane", "pane_list", "mixed"] = "single_implicit_pane"
        elif all(isinstance(pane, _ExplicitPanePlan) for pane in panes):
            mode = "pane_list"
        else:
            mode = "mixed"
        return _WindowPlan(mode=mode, panes=tuple(panes))

    def _compile_window(
        self,
        location: _WindowLocation,
        session_name: str,
    ) -> WindowSpec:
        window = location.node
        ancestor_nodes = location.path[:-1]
        ancestor_layers = tuple(self._node_layer(node) for node in ancestor_nodes)
        window_layers = (*ancestor_layers, self._node_layer(window))
        window_name = self._render_node_text(
            window.text,
            window_layers,
            builtins={"session-name": session_name},
            subject=f"window name from node {window.id!r}",
            default="window",
        )
        plan = self._plan_window(window)
        compiled_panes: list[PaneSpec] = []
        for pane_plan in plan.panes:
            if isinstance(pane_plan, _ImplicitPanePlan):
                compiled_panes.append(
                    self._compile_implicit_pane(
                        window,
                        ancestor_layers,
                        session_name,
                        window_name,
                        command_nodes=pane_plan.command_nodes,
                        ordinal=pane_plan.ordinal,
                        expands_window_root=pane_plan.expands_window_root,
                    )
                )
            else:
                compiled_panes.append(
                    self._compile_pane_root(
                        window,
                        pane_plan.pane_root,
                        window_layers,
                        session_name,
                        window_name,
                    )
                )

        return WindowSpec(
            window_id=window.id,
            name=window_name,
            mode=plan.mode,
            panes=tuple(compiled_panes),
        )

    def _builtins(
        self,
        *,
        session_name: str,
        window_name: str,
        pane_name: str | None,
        node_name: str,
    ) -> dict[str, str]:
        return {
            "session-name": session_name,
            "window-name": window_name,
            "pane-name": pane_name or "",
            "node-name": node_name,
        }

    def _layers_for_node(
        self,
        inherited_layers: tuple[ScopeLayer, ...],
        node: RawNode,
        *,
        include_local_layer: bool,
    ) -> tuple[ScopeLayer, ...]:
        if not include_local_layer:
            return inherited_layers
        return (*inherited_layers, self._node_layer(node))

    def _layers_for_relationship(
        self,
        inherited_layers: tuple[ScopeLayer, ...],
        callsite: RawNode,
        target: RawNode,
        *,
        include_callsite_layer: bool,
    ) -> tuple[ScopeLayer, ...]:
        layers = [*inherited_layers, self._node_layer(target)]
        if include_callsite_layer:
            layers.append(self._node_layer(callsite))
        return tuple(layers)

    def _compile_implicit_pane(
        self,
        window: RawNode,
        ancestor_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        *,
        command_nodes: tuple[RawNode, ...],
        ordinal: int,
        expands_window_root: bool,
    ) -> PaneSpec:
        effective_layers = self._layers_for_node(
            ancestor_layers,
            window,
            include_local_layer=True,
        )
        base_scope = self._resolver.resolve(
            effective_layers,
            builtins=self._builtins(
                session_name=session_name,
                window_name=window_name,
                pane_name=None,
                node_name=window_name if (window.detail or window.relationships) else "",
            ),
            strict=False,
            subject=f"implicit pane for window {window.text!r}",
        )

        if expands_window_root:
            steps = self._expand_node(
                window,
                inherited_layers=ancestor_layers,
                session_name=session_name,
                window_name=window_name,
                pane_name=None,
                relationship_stack=(),
                allow_text_payload=False,
            )
        else:
            window_layers = (*ancestor_layers, self._node_layer(window))
            expanded: list[CommandStep] = []
            for child in command_nodes:
                expanded.extend(
                    self._expand_node(
                        child,
                        inherited_layers=window_layers,
                        session_name=session_name,
                        window_name=window_name,
                        pane_name=None,
                        relationship_stack=(),
                    )
                )
            steps = expanded

        return PaneSpec(
            pane_id=f"{window.id}::__implicit__:{ordinal}",
            title=None,
            base_scope=base_scope,
            steps=tuple(steps),
        )

    def _compile_pane_root(
        self,
        window: RawNode,
        pane_root: RawNode,
        window_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
    ) -> PaneSpec:
        children = self._non_alias_children(pane_root)
        structural_root = bool(children and not pane_root.detail and not pane_root.relationships)
        has_named_pane = bool(
            children or pane_root.detail or pane_root.relationships or "PANE" in pane_root.tags
        )

        base_layers = self._layers_for_node(
            window_layers,
            pane_root,
            include_local_layer=True,
        )
        title = None
        if has_named_pane:
            title = (
                self._render_node_text(
                    pane_root.text,
                    base_layers,
                    builtins={
                        "session-name": session_name,
                        "window-name": window_name,
                    },
                    subject=f"pane name from node {pane_root.id!r}",
                )
                or None
            )

        base_scope = self._resolver.resolve(
            base_layers,
            builtins=self._builtins(
                session_name=session_name,
                window_name=window_name,
                pane_name=title,
                node_name=title or pane_root.text,
            ),
            strict=False,
            subject=f"pane {pane_root.text!r}",
        )

        if structural_root:
            inherited = (*window_layers, self._node_layer(pane_root))
            steps: list[CommandStep] = []
            for child in children:
                steps.extend(
                    self._expand_node(
                        child,
                        inherited_layers=inherited,
                        session_name=session_name,
                        window_name=window_name,
                        pane_name=title,
                        relationship_stack=(),
                    )
                )
        else:
            steps = self._expand_node(
                pane_root,
                inherited_layers=window_layers,
                session_name=session_name,
                window_name=window_name,
                pane_name=title,
                relationship_stack=(),
                allow_text_payload=False,
            )

        return PaneSpec(
            pane_id=pane_root.id,
            title=title,
            base_scope=base_scope,
            steps=tuple(steps),
        )

    def _expand_relationship_target(
        self,
        target: RawNode,
        *,
        inherited_layers: tuple[ScopeLayer, ...],
        callsite: RawNode,
        include_callsite_layer: bool,
        session_name: str,
        window_name: str,
        pane_name: str | None,
        callsite_node_name: str,
        relationship_stack: tuple[str, ...],
    ) -> list[CommandStep]:
        if target.id in relationship_stack:
            cycle = " -> ".join([*relationship_stack, target.id])
            raise SemanticError(f"relationship cycle detected: {cycle}")

        relationship_layers = self._layers_for_relationship(
            inherited_layers,
            callsite,
            target,
            include_callsite_layer=include_callsite_layer,
        )
        return self._expand_node(
            target,
            inherited_layers=relationship_layers,
            session_name=session_name,
            window_name=window_name,
            pane_name=pane_name,
            relationship_stack=(*relationship_stack, target.id),
            node_name_override=callsite_node_name,
            include_local_layer=False,
            function_root=True,
        )

    def _expand_node(
        self,
        node: RawNode,
        *,
        inherited_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        pane_name: str | None,
        relationship_stack: tuple[str, ...],
        node_name_override: str | None = None,
        include_local_layer: bool = True,
        function_root: bool = False,
        allow_text_payload: bool = True,
    ) -> list[CommandStep]:
        targets = self._relationship_targets(node)
        local_layers = self._layers_for_node(
            inherited_layers,
            node,
            include_local_layer=include_local_layer,
        )
        node_name_template = node_name_override if node_name_override is not None else node.text
        node_name = self._render_node_text(
            node_name_template,
            local_layers,
            builtins={
                "session-name": session_name,
                "window-name": window_name,
                "pane-name": pane_name or "",
            },
            subject=f"node name from node {node.id!r}",
        )
        children = self._non_alias_children(node)
        uses_text_payload = bool(
            allow_text_payload
            and node.text.strip()
            and not node.detail
            and (not function_root or not children)
        )
        has_direct_payload = bool(node.detail and node.detail.strip()) or uses_text_payload
        scope = self._resolver.resolve(
            local_layers,
            builtins=self._builtins(
                session_name=session_name,
                window_name=window_name,
                pane_name=pane_name,
                node_name=node_name,
            ),
            strict=has_direct_payload,
            subject=f"node {node.id!r}",
        )

        steps: list[CommandStep] = []

        if node.detail and node.detail.strip():
            steps.extend(
                self._command_steps(
                    node,
                    template=node.detail,
                    payload_source="detail",
                    scope=scope,
                )
            )
        elif uses_text_payload:
            steps.extend(
                self._command_steps(
                    node,
                    template=node.text,
                    payload_source="text",
                    scope=scope,
                )
            )

        for target in targets:
            steps.extend(
                self._expand_relationship_target(
                    target,
                    inherited_layers=inherited_layers,
                    callsite=node,
                    include_callsite_layer=include_local_layer,
                    session_name=session_name,
                    window_name=window_name,
                    pane_name=pane_name,
                    callsite_node_name=node_name,
                    relationship_stack=relationship_stack,
                )
            )

        for child in children:
            steps.extend(
                self._expand_node(
                    child,
                    inherited_layers=local_layers,
                    session_name=session_name,
                    window_name=window_name,
                    pane_name=pane_name,
                    relationship_stack=relationship_stack,
                    node_name_override=node_name_override,
                )
            )
        return steps

    def _command_steps(
        self,
        node: RawNode,
        *,
        template: str,
        payload_source: Literal["text", "detail", "relationship"],
        scope,
    ) -> list[CommandStep]:
        commands = self._resolver.render_command(
            template,
            scope,
            subject=f"command in node {node.id!r}",
        )
        return [
            CommandStep(
                node_id=node.id,
                display_name=scope.vars.get("node-name", node.text),
                payload_source=payload_source,
                command=command,
                effective_scope=scope,
            )
            for command in commands
        ]
