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
    ScopeSnapshot,
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
    source_window: RawNode
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


@dataclass(frozen=True)
class _WindowInheritanceSpec:
    scope_layers: tuple[ScopeLayer, ...]
    attribute_layers: tuple[dict[str, str], ...]
    panes: tuple[_PanePlan, ...]


class MindmapCompiler:
    """Normalize a raw Freeplane tree into an execution-oriented session plan."""

    def __init__(self, root: RawNode):
        self.root = root
        self.index: dict[str, RawNode] = {}
        self._resolver = ScopeResolver(self.root)
        self._window_inheritance_cache: dict[str, _WindowInheritanceSpec] = {}
        self._build_index(root)
        self._validate_relationship_targets()
        for location in self._collect_windows():
            self._resolve_window_inheritance(location.node, inheritance_stack=())

    def compile(self) -> SessionSpec:
        root_layers = (self._node_layer(self.root),)
        session_name = self._render_node_text(
            self.root.text,
            root_layers,
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
            session_name=session_name,
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

    def _helper_relationship_targets(self, node: RawNode) -> tuple[RawNode, ...]:
        if "WINDOW" in node.tags:
            return ()
        return self._relationship_targets(node)

    def _window_inheritance_targets(self, window: RawNode) -> tuple[RawNode, ...]:
        if "WINDOW" not in window.tags:
            return ()
        targets = self._relationship_targets(window)
        for target in targets:
            if target.id == window.id:
                raise SemanticError(
                    f"window {window.id!r} ({window.text!r}) cannot inherit from itself"
                )
            if "WINDOW" not in target.tags:
                raise SemanticError(
                    f"window {window.id!r} ({window.text!r}) relationship target "
                    f"{target.id!r} ({target.text!r}) must be a WINDOW node"
                )
        return targets

    def _node_layer(self, node: RawNode) -> ScopeLayer:
        attributes = split_attributes(node.attributes)
        aliases = self._aliases_declared_by(node)
        return combine_layer(attributes, aliases=aliases)

    @staticmethod
    def _attribute_dict(node: RawNode) -> dict[str, str]:
        return {key: to_string(value) for key, value in node.attributes.items()}

    @staticmethod
    def _merge_attribute_layers(attribute_layers: tuple[dict[str, str], ...]) -> dict[str, str]:
        merged: dict[str, str] = {}
        for layer in attribute_layers:
            merged.update(layer)
        return merged

    def _context_builtins(
        self,
        *,
        session_name: str | None = None,
        window_name: str | None = None,
        pane_name: str | None = None,
        node_name: str | None = None,
        window_attributes: dict[str, str] | None = None,
        pane_attributes: dict[str, str] | None = None,
    ) -> dict[str, str]:
        builtins: dict[str, str] = {}
        if session_name is not None:
            builtins["session-name"] = session_name
        if node_name is not None:
            builtins["node-name"] = node_name
        if window_name is not None:
            builtins["window.name"] = window_name
        if pane_name is not None:
            builtins["pane.name"] = pane_name
        if window_attributes:
            for key, value in window_attributes.items():
                if key != "name":
                    builtins[f"window.{key}"] = value
        if pane_attributes:
            for key, value in pane_attributes.items():
                if key != "name":
                    builtins[f"pane.{key}"] = value
        return builtins

    def _resolve_scope(
        self,
        layers: tuple[ScopeLayer, ...],
        *,
        strict: bool,
        subject: str,
        session_name: str | None = None,
        window_name: str | None = None,
        pane_name: str | None = None,
        node_name: str | None = None,
        window_attributes: dict[str, str] | None = None,
        pane_attributes: dict[str, str] | None = None,
    ) -> ScopeSnapshot:
        return self._resolver.resolve(
            layers,
            builtins=self._context_builtins(
                session_name=session_name,
                window_name=window_name,
                pane_name=pane_name,
                node_name=node_name,
                window_attributes=window_attributes,
                pane_attributes=pane_attributes,
            ),
            strict=strict,
            subject=subject,
        )

    def _render_node_text(
        self,
        template: str,
        layers: tuple[ScopeLayer, ...],
        *,
        subject: str,
        default: str = "",
        session_name: str | None = None,
        window_name: str | None = None,
        pane_name: str | None = None,
        node_name: str | None = None,
        window_attributes: dict[str, str] | None = None,
        pane_attributes: dict[str, str] | None = None,
    ) -> str:
        if not template.strip():
            return default
        scope = self._resolve_scope(
            layers,
            strict=False,
            subject=subject,
            session_name=session_name,
            window_name=window_name,
            pane_name=pane_name,
            node_name=node_name,
            window_attributes=window_attributes,
            pane_attributes=pane_attributes,
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
        targets = self._helper_relationship_targets(alias_node)

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
        targets = self._helper_relationship_targets(node)
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
        """Classify one direct WINDOW child using the map grammar."""

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

    def _plan_local_window(self, window: RawNode) -> _WindowPlan:
        attributes = {key: to_string(value) for key, value in window.attributes.items()}
        explicit = attributes.get("window-mode") or attributes.get("window_mode")
        children = tuple(self._non_alias_children(window))

        if window.detail:
            if explicit in {"pane-list", "pane_list"} and children:
                raise SemanticError(
                    f"window {window.id!r} combines a root command with explicit pane-list mode; "
                    "use single-pane or move the command into a pane"
                )
            return _WindowPlan(
                mode="single_implicit_pane",
                panes=(
                    _ImplicitPanePlan(
                        source_window=window,
                        command_nodes=(),
                        ordinal=0,
                        expands_window_root=True,
                    ),
                ),
            )

        if explicit in {"single-pane", "single_implicit_pane"}:
            return _WindowPlan(
                mode="single_implicit_pane",
                panes=(
                    _ImplicitPanePlan(
                        source_window=window,
                        command_nodes=children,
                        ordinal=0,
                    ),
                ),
            )

        if explicit in {"pane-list", "pane_list"}:
            return _WindowPlan(
                mode="pane_list",
                panes=tuple(_ExplicitPanePlan(child) for child in children),
            )

        if explicit:
            raise SemanticError(f"unsupported window mode {explicit!r} in window {window.id!r}")

        panes: list[_PanePlan] = []
        pending_commands: list[RawNode] = []
        implicit_ordinal = 0

        def flush_commands() -> None:
            nonlocal implicit_ordinal
            if not pending_commands:
                return
            panes.append(
                _ImplicitPanePlan(
                    source_window=window,
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

        return _WindowPlan(mode=self._mode_for_plans(tuple(panes)), panes=tuple(panes))

    @staticmethod
    def _mode_for_plans(
        plans: tuple[_PanePlan, ...],
    ) -> Literal["single_implicit_pane", "pane_list", "mixed"]:
        if not plans:
            return "pane_list"
        if len(plans) == 1 and isinstance(plans[0], _ImplicitPanePlan):
            return "single_implicit_pane"
        if all(isinstance(plan, _ExplicitPanePlan) for plan in plans):
            return "pane_list"
        return "mixed"

    def _resolve_window_inheritance(
        self,
        window: RawNode,
        *,
        inheritance_stack: tuple[RawNode, ...],
    ) -> _WindowInheritanceSpec:
        cached = self._window_inheritance_cache.get(window.id)
        if cached is not None:
            return cached

        if window in inheritance_stack:
            cycle_nodes = (*inheritance_stack, window)
            cycle = " -> ".join(f"{node.id}:{node.text}" for node in cycle_nodes)
            raise SemanticError(f"window inheritance cycle detected: {cycle}")

        targets = self._window_inheritance_targets(window)
        next_stack = (*inheritance_stack, window)

        scope_layers: list[ScopeLayer] = []
        attribute_layers: list[dict[str, str]] = []
        panes: list[_PanePlan] = []

        for target in targets:
            inherited = self._resolve_window_inheritance(target, inheritance_stack=next_stack)
            scope_layers.extend(inherited.scope_layers)
            attribute_layers.extend(inherited.attribute_layers)
            panes.extend(inherited.panes)

        scope_layers.append(self._node_layer(window))
        attribute_layers.append(self._attribute_dict(window))
        panes.extend(self._plan_local_window(window).panes)

        result = _WindowInheritanceSpec(
            scope_layers=tuple(scope_layers),
            attribute_layers=tuple(attribute_layers),
            panes=tuple(panes),
        )
        self._window_inheritance_cache[window.id] = result
        return result

    def _compile_window(
        self,
        location: _WindowLocation,
        session_name: str,
    ) -> WindowSpec:
        window = location.node
        ancestor_nodes = location.path[:-1]
        ancestor_layers = tuple(self._node_layer(node) for node in ancestor_nodes)
        inherited = self._resolve_window_inheritance(window, inheritance_stack=())
        merged_window_attributes = self._merge_attribute_layers(inherited.attribute_layers)
        window_layers = (*ancestor_layers, *inherited.scope_layers)
        window_name = self._render_node_text(
            window.text,
            window_layers,
            session_name=session_name,
            window_attributes=merged_window_attributes,
            subject=f"window name from node {window.id!r}",
            default="window",
        )
        merged_panes = self._merge_window_panes(
            inherited.panes,
            window_layers=window_layers,
            session_name=session_name,
            window_name=window_name,
            window_attributes=merged_window_attributes,
        )
        compiled_panes: list[PaneSpec] = []
        for pane_index, pane_plan in enumerate(merged_panes):
            if isinstance(pane_plan, _ImplicitPanePlan):
                compiled_panes.append(
                    self._compile_implicit_pane(
                        pane_plan,
                        pane_index=pane_index,
                        derived_window=window,
                        window_layers=window_layers,
                        session_name=session_name,
                        window_name=window_name,
                        window_attributes=merged_window_attributes,
                    )
                )
            else:
                compiled_panes.append(
                    self._compile_pane_root(
                        pane_plan.pane_root,
                        window_layers=window_layers,
                        session_name=session_name,
                        window_name=window_name,
                        window_attributes=merged_window_attributes,
                    )
                )

        return WindowSpec(
            window_id=window.id,
            name=window_name,
            mode=self._mode_for_plans(merged_panes),
            panes=tuple(compiled_panes),
        )

    def _render_pane_title(
        self,
        pane_root: RawNode,
        *,
        window_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        window_attributes: dict[str, str],
    ) -> str | None:
        children = self._non_alias_children(pane_root)
        has_named_pane = bool(
            children or pane_root.detail or pane_root.relationships or "PANE" in pane_root.tags
        )
        if not has_named_pane:
            return None
        pane_attributes = self._attribute_dict(pane_root)
        title = self._render_node_text(
            pane_root.text,
            (*window_layers, self._node_layer(pane_root)),
            session_name=session_name,
            window_name=window_name,
            window_attributes=window_attributes,
            pane_attributes=pane_attributes,
            subject=f"pane name from node {pane_root.id!r}",
        )
        return title or None

    def _merge_window_panes(
        self,
        pane_plans: tuple[_PanePlan, ...],
        *,
        window_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        window_attributes: dict[str, str],
    ) -> tuple[_PanePlan, ...]:
        merged: list[_PanePlan] = []
        explicit_by_name: dict[str, int] = {}

        for plan in pane_plans:
            pane_name: str | None = None
            if isinstance(plan, _ExplicitPanePlan):
                pane_name = self._render_pane_title(
                    plan.pane_root,
                    window_layers=window_layers,
                    session_name=session_name,
                    window_name=window_name,
                    window_attributes=window_attributes,
                )
            if pane_name:
                previous_index = explicit_by_name.get(pane_name)
                if previous_index is not None:
                    merged.pop(previous_index)
                    explicit_by_name = {
                        name: (index - 1 if index > previous_index else index)
                        for name, index in explicit_by_name.items()
                        if name != pane_name
                    }
                explicit_by_name[pane_name] = len(merged)
            merged.append(plan)

        return tuple(merged)

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
        pane_plan: _ImplicitPanePlan,
        *,
        pane_index: int,
        derived_window: RawNode,
        window_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        window_attributes: dict[str, str],
    ) -> PaneSpec:
        base_scope = self._resolve_scope(
            window_layers,
            strict=False,
            subject=f"implicit pane for window {derived_window.text!r}",
            session_name=session_name,
            window_name=window_name,
            pane_name="",
            node_name=window_name if pane_plan.expands_window_root else "",
            window_attributes=window_attributes,
        )

        if pane_plan.expands_window_root:
            steps = self._expand_node(
                pane_plan.source_window,
                inherited_layers=window_layers,
                session_name=session_name,
                window_name=window_name,
                window_attributes=window_attributes,
                pane_name=None,
                pane_attributes=None,
                relationship_stack=(),
                include_local_layer=False,
                allow_text_payload=False,
            )
        else:
            expanded: list[CommandStep] = []
            for child in pane_plan.command_nodes:
                expanded.extend(
                    self._expand_node(
                        child,
                        inherited_layers=window_layers,
                        session_name=session_name,
                        window_name=window_name,
                        window_attributes=window_attributes,
                        pane_name=None,
                        pane_attributes=None,
                        relationship_stack=(),
                    )
                )
            steps = expanded

        return PaneSpec(
            pane_id=f"{derived_window.id}::__implicit__:{pane_index}",
            title=None,
            base_scope=base_scope,
            steps=tuple(steps),
        )

    def _compile_pane_root(
        self,
        pane_root: RawNode,
        *,
        window_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        window_attributes: dict[str, str],
    ) -> PaneSpec:
        children = self._non_alias_children(pane_root)
        structural_root = bool(children and not pane_root.detail and not pane_root.relationships)
        pane_attributes = self._attribute_dict(pane_root)
        base_layers = self._layers_for_node(
            window_layers,
            pane_root,
            include_local_layer=True,
        )
        title = self._render_pane_title(
            pane_root,
            window_layers=window_layers,
            session_name=session_name,
            window_name=window_name,
            window_attributes=window_attributes,
        )

        base_scope = self._resolve_scope(
            base_layers,
            strict=False,
            subject=f"pane {pane_root.text!r}",
            session_name=session_name,
            window_name=window_name,
            pane_name=title or "",
            node_name=title or pane_root.text,
            window_attributes=window_attributes,
            pane_attributes=pane_attributes,
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
                        window_attributes=window_attributes,
                        pane_name=title,
                        pane_attributes=pane_attributes,
                        relationship_stack=(),
                    )
                )
        else:
            steps = self._expand_node(
                pane_root,
                inherited_layers=window_layers,
                session_name=session_name,
                window_name=window_name,
                window_attributes=window_attributes,
                pane_name=title,
                pane_attributes=pane_attributes,
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
        window_attributes: dict[str, str],
        pane_name: str | None,
        pane_attributes: dict[str, str] | None,
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
            window_attributes=window_attributes,
            pane_name=pane_name,
            pane_attributes=pane_attributes,
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
        window_attributes: dict[str, str],
        pane_name: str | None,
        pane_attributes: dict[str, str] | None,
        relationship_stack: tuple[str, ...],
        node_name_override: str | None = None,
        include_local_layer: bool = True,
        function_root: bool = False,
        allow_text_payload: bool = True,
    ) -> list[CommandStep]:
        targets = self._helper_relationship_targets(node)
        local_layers = self._layers_for_node(
            inherited_layers,
            node,
            include_local_layer=include_local_layer,
        )
        node_name_template = node_name_override if node_name_override is not None else node.text
        node_name = self._render_node_text(
            node_name_template,
            local_layers,
            session_name=session_name,
            window_name=window_name,
            pane_name=(pane_name or "") if pane_name is not None else "",
            window_attributes=window_attributes,
            pane_attributes=pane_attributes,
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
        scope = self._resolve_scope(
            local_layers,
            strict=has_direct_payload,
            subject=f"node {node.id!r}",
            session_name=session_name,
            window_name=window_name,
            pane_name=(pane_name or "") if pane_name is not None else "",
            node_name=node_name,
            window_attributes=window_attributes,
            pane_attributes=pane_attributes,
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
                    window_attributes=window_attributes,
                    pane_name=pane_name,
                    pane_attributes=pane_attributes,
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
                    window_attributes=window_attributes,
                    pane_name=pane_name,
                    pane_attributes=pane_attributes,
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
        scope: ScopeSnapshot,
    ) -> list[CommandStep]:
        if "\n" in template:
            command = self._resolver.render_command_block(
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
            ]

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
