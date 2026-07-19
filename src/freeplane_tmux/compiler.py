from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

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


class MindmapCompiler:
    """Normalize a raw Freeplane tree into an execution-oriented session plan."""

    def __init__(self, root: RawNode):
        self.root = root
        self.index: dict[str, RawNode] = {}
        self._resolver = ScopeResolver()
        self._build_index(root)
        self._validate_relationship_targets()

    def compile(self) -> SessionSpec:
        session_name = self.root.text.strip() or "freeplane"
        windows = tuple(
            self._compile_window(location, session_name) for location in self._collect_windows()
        )
        return SessionSpec(
            session_id=self.root.id,
            session_name=session_name,
            windows=windows,
        )

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

    def _window_mode(self, window: RawNode) -> Literal["single_implicit_pane", "pane_list"]:
        attributes = {key: to_string(value) for key, value in window.attributes.items()}
        explicit = attributes.get("window-mode") or attributes.get("window_mode")
        children = self._non_alias_children(window)

        # A root invocation without pane children is always one implicit pane.
        # This is a semantic guarantee, even if a stale pane-list override exists.
        if (window.detail or window.relationships) and not children:
            return "single_implicit_pane"
        if explicit in {"single-pane", "single_implicit_pane"}:
            return "single_implicit_pane"
        if explicit in {"pane-list", "pane_list"}:
            if window.detail or window.relationships:
                raise SemanticError(
                    f"window {window.id!r} combines a root command/relationship with "
                    "explicit pane-list mode; use single-pane or move the command into a pane"
                )
            return "pane_list"

        if window.detail or window.relationships:
            return "single_implicit_pane"
        if not children:
            return "pane_list"

        all_plain_commands = all(
            not child.children
            and not child.detail
            and not child.relationships
            and not child.attributes
            and "WINDOW" not in child.tags
            for child in children
        )
        return "single_implicit_pane" if all_plain_commands else "pane_list"

    def _compile_window(
        self,
        location: _WindowLocation,
        session_name: str,
    ) -> WindowSpec:
        window = location.node
        ancestor_nodes = location.path[:-1]
        ancestor_layers = tuple(self._node_layer(node) for node in ancestor_nodes)
        mode = self._window_mode(window)

        if mode == "single_implicit_pane":
            panes = (self._compile_implicit_pane(window, ancestor_layers, session_name),)
        else:
            window_layers = (*ancestor_layers, self._node_layer(window))
            panes = tuple(
                self._compile_pane_root(
                    window,
                    pane_root,
                    window_layers,
                    session_name,
                )
                for pane_root in self._non_alias_children(window)
            )

        return WindowSpec(
            window_id=window.id,
            name=window.text.strip() or "window",
            mode=mode,
            panes=panes,
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
                window_name=window.text,
                pane_name=None,
                node_name=window.text if (window.detail or window.relationships) else "",
            ),
            strict=False,
            subject=f"implicit pane for window {window.text!r}",
        )

        if window.detail or window.relationships:
            steps = self._expand_node(
                window,
                inherited_layers=ancestor_layers,
                session_name=session_name,
                window_name=window.text,
                pane_name=None,
                relationship_stack=(),
                allow_text_payload=False,
            )
        else:
            window_layers = (*ancestor_layers, self._node_layer(window))
            expanded: list[CommandStep] = []
            for child in self._non_alias_children(window):
                expanded.extend(
                    self._expand_node(
                        child,
                        inherited_layers=window_layers,
                        session_name=session_name,
                        window_name=window.text,
                        pane_name=None,
                        relationship_stack=(),
                    )
                )
            steps = expanded

        return PaneSpec(
            pane_id=f"{window.id}::__implicit__",
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
    ) -> PaneSpec:
        children = self._non_alias_children(pane_root)
        structural_root = bool(children and not pane_root.detail and not pane_root.relationships)
        has_named_pane = bool(children or pane_root.detail or pane_root.relationships)
        title = (pane_root.text.strip() or None) if has_named_pane else None

        base_layers = self._layers_for_node(
            window_layers,
            pane_root,
            include_local_layer=True,
        )
        base_scope = self._resolver.resolve(
            base_layers,
            builtins=self._builtins(
                session_name=session_name,
                window_name=window.text,
                pane_name=title,
                node_name=pane_root.text,
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
                        window_name=window.text,
                        pane_name=title,
                        relationship_stack=(),
                    )
                )
        else:
            steps = self._expand_node(
                pane_root,
                inherited_layers=window_layers,
                session_name=session_name,
                window_name=window.text,
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
        node_name = node_name_override if node_name_override is not None else node.text
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
                display_name=node.text,
                payload_source=payload_source,
                command=command,
                effective_scope=scope,
            )
            for command in commands
        ]
