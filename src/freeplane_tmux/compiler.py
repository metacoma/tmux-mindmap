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
from .scope import (
    RuntimeTemplateContext,
    ScopeResolver,
    combine_layer,
    compile_vars_namespace,
    split_attributes,
    to_string,
)
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
    runtime_attribute_layers: tuple[dict[str, str], ...]
    panes: tuple[_PanePlan, ...]
    layouts: tuple[str, ...]


class MindmapCompiler:
    """Normalize a raw Freeplane tree into an execution-oriented session plan."""

    def __init__(self, root: RawNode):
        self.root = root
        self.index: dict[str, RawNode] = {}
        self._compiled_vars = compile_vars_namespace(root)
        self._resolver = ScopeResolver(self._compiled_vars)
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
            subject=self._field_subject(self.root, "text"),
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
        session_name: str | None = None,
    ) -> str | None:
        raw_workdir = to_string(self.root.attributes.get("exec.workdir", ""))
        if not raw_workdir.strip():
            return None
        rendered = self._render_node_text(
            raw_workdir,
            root_layers,
            subject=self._field_subject(self.root, "attributes.exec.workdir"),
            session_name=session_name,
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
                if node is self.root and child.text == "vars":
                    continue
                walk(child, current_path, inside_window)

        walk(self.root, (), False)
        return tuple(found)

    @staticmethod
    def _field_subject(node: RawNode, field_name: str) -> str:
        return f'Node {node.id} "{node.text}" field "{field_name}"'

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
        attributes = split_attributes(node.attributes, node_id=node.id, node_text=node.text)
        aliases = self._aliases_declared_by(node)
        return combine_layer(attributes, aliases=aliases)

    def _local_template_bindings(self, node: RawNode) -> dict[str, str]:
        return dict(self._node_layer(node).runtime_attrs)

    def _relationship_target_defaults(self, target: RawNode) -> dict[str, str]:
        defaults = dict(self._node_layer(target).helper_defaults)
        defaults.update(self._local_template_bindings(target))
        return defaults

    def _relationship_callsite_overrides(self, callsite: RawNode) -> dict[str, str]:
        overrides = dict(self._node_layer(callsite).call_args)
        overrides.update(self._local_template_bindings(callsite))
        return overrides

    def _runtime_attribute_dict(self, node: RawNode) -> dict[str, str]:
        return dict(self._node_layer(node).runtime_attrs)

    @staticmethod
    def _merge_attribute_layers(attribute_layers: tuple[dict[str, str], ...]) -> dict[str, str]:
        merged: dict[str, str] = {}
        for layer in attribute_layers:
            merged.update(layer)
        return merged

    def _runtime_context(
        self,
        *,
        session_name: str | None = None,
        window_name: str | None = None,
        window_id: str | None = None,
        window_attributes: dict[str, str] | None = None,
        pane_name: str | None = None,
        pane_id: str | None = None,
        pane_attributes: dict[str, str] | None = None,
        node_name: str | None = None,
        node_id: str | None = None,
        node_attributes: dict[str, str] | None = None,
    ) -> RuntimeTemplateContext:
        scalars: dict[str, str] = {
            "session.name": session_name or self.root.text,
            "session.id": self.root.id,
        }
        object_fields: dict[str, tuple[str, ...]] = {
            "session": tuple(["name", "id", *self._runtime_attribute_dict(self.root).keys()]),
        }
        for key, value in self._runtime_attribute_dict(self.root).items():
            scalars[f"session.{key}"] = value

        def add_object(
            name: str,
            *,
            object_name: str | None,
            object_id: str | None,
            attrs: dict[str, str] | None,
        ) -> None:
            fields: list[str] = []
            if object_name is not None:
                scalars[f"{name}.name"] = object_name
                fields.append("name")
            if object_id is not None:
                scalars[f"{name}.id"] = object_id
                fields.append("id")
            if attrs:
                for key, value in attrs.items():
                    scalars[f"{name}.{key}"] = value
                    fields.append(key)
            if fields:
                object_fields[name] = tuple(fields)

        add_object(
            "window",
            object_name=window_name,
            object_id=window_id,
            attrs=window_attributes,
        )
        add_object(
            "pane",
            object_name=pane_name,
            object_id=pane_id,
            attrs=pane_attributes,
        )
        add_object(
            "node",
            object_name=node_name,
            object_id=node_id,
            attrs=node_attributes,
        )

        return RuntimeTemplateContext(scalars=scalars, object_fields=object_fields)

    def _resolve_scope(
        self,
        layers: tuple[ScopeLayer, ...],
        *,
        strict: bool,
        subject: str,
        session_name: str | None = None,
        window_name: str | None = None,
        window_id: str | None = None,
        window_attributes: dict[str, str] | None = None,
        pane_name: str | None = None,
        pane_id: str | None = None,
        pane_attributes: dict[str, str] | None = None,
        node_name: str | None = None,
        node_id: str | None = None,
        node_attributes: dict[str, str] | None = None,
        args_namespace: dict[str, str] | None = None,
        local_bindings: dict[str, str] | None = None,
    ) -> ScopeSnapshot:
        return self._resolver.resolve(
            layers,
            runtime_context=self._runtime_context(
                session_name=session_name,
                window_name=window_name,
                window_id=window_id,
                window_attributes=window_attributes,
                pane_name=pane_name,
                pane_id=pane_id,
                pane_attributes=pane_attributes,
                node_name=node_name,
                node_id=node_id,
                node_attributes=node_attributes,
            ),
            args_namespace=args_namespace,
            local_bindings=local_bindings,
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
        window_id: str | None = None,
        window_attributes: dict[str, str] | None = None,
        pane_name: str | None = None,
        pane_id: str | None = None,
        pane_attributes: dict[str, str] | None = None,
        node_name: str | None = None,
        node_id: str | None = None,
        node_attributes: dict[str, str] | None = None,
        args_namespace: dict[str, str] | None = None,
        local_bindings: dict[str, str] | None = None,
    ) -> str:
        if not template.strip():
            return default
        scope = self._resolve_scope(
            layers,
            strict=False,
            subject=subject,
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
            pane_name=pane_name,
            pane_id=pane_id,
            pane_attributes=pane_attributes,
            node_name=node_name,
            node_id=node_id,
            node_attributes=node_attributes,
            args_namespace=args_namespace,
            local_bindings=local_bindings,
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
        explicit = self._node_layer(window).tmux_mode
        children = tuple(self._non_alias_children(window))

        if window.detail:
            if explicit == "pane-list" and children:
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

        if explicit == "single-pane":
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

        if explicit == "pane-list":
            return _WindowPlan(
                mode="pane_list",
                panes=tuple(_ExplicitPanePlan(child) for child in children),
            )

        if explicit:
            raise SemanticError(f"unsupported tmux.mode {explicit!r} in window {window.id!r}")

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
        runtime_attribute_layers: list[dict[str, str]] = []
        panes: list[_PanePlan] = []
        layouts: list[str] = []

        for target in targets:
            inherited = self._resolve_window_inheritance(target, inheritance_stack=next_stack)
            scope_layers.extend(inherited.scope_layers)
            runtime_attribute_layers.extend(inherited.runtime_attribute_layers)
            panes.extend(inherited.panes)
            layouts.extend(inherited.layouts)

        local_layer = self._node_layer(window)
        scope_layers.append(local_layer)
        runtime_attribute_layers.append(dict(local_layer.runtime_attrs))
        panes.extend(self._plan_local_window(window).panes)
        if local_layer.tmux_layout:
            layouts.append(local_layer.tmux_layout)

        result = _WindowInheritanceSpec(
            scope_layers=tuple(scope_layers),
            runtime_attribute_layers=tuple(runtime_attribute_layers),
            panes=tuple(panes),
            layouts=tuple(layouts),
        )
        self._window_inheritance_cache[window.id] = result
        return result

    def _compile_window(
        self,
        location: _WindowLocation,
        session_name: str,
    ) -> WindowSpec:
        window = location.node
        ancestor_nodes = tuple(
            node for node in location.path[:-1] if node is not self.root or node.text != "vars"
        )
        ancestor_layers = tuple(self._node_layer(node) for node in ancestor_nodes)
        inherited = self._resolve_window_inheritance(window, inheritance_stack=())
        merged_window_attributes = self._merge_attribute_layers(inherited.runtime_attribute_layers)
        window_layers = (*ancestor_layers, *inherited.scope_layers)
        window_name = self._render_node_text(
            window.text,
            window_layers,
            session_name=session_name,
            window_id=window.id,
            window_attributes=merged_window_attributes,
            subject=self._field_subject(window, "text"),
            default="window",
        )
        merged_panes = self._merge_window_panes(
            inherited.panes,
            window_layers=window_layers,
            session_name=session_name,
            window_name=window_name,
            window_id=window.id,
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
                        window_id=window.id,
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
                        window_id=window.id,
                        window_attributes=merged_window_attributes,
                    )
                )

        return WindowSpec(
            window_id=window.id,
            name=window_name,
            mode=self._mode_for_plans(merged_panes),
            layout=inherited.layouts[-1] if inherited.layouts else None,
            panes=tuple(compiled_panes),
        )

    def _render_pane_title(
        self,
        pane_root: RawNode,
        *,
        window_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        window_id: str,
        window_attributes: dict[str, str],
    ) -> str | None:
        children = self._non_alias_children(pane_root)
        has_named_pane = bool(
            children or pane_root.detail or pane_root.relationships or "PANE" in pane_root.tags
        )
        if not has_named_pane:
            return None
        pane_attributes = self._runtime_attribute_dict(pane_root)
        title = self._render_node_text(
            pane_root.text,
            (*window_layers, self._node_layer(pane_root)),
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
            pane_id=pane_root.id,
            pane_attributes=pane_attributes,
            subject=self._field_subject(pane_root, "text"),
        )
        return title or None

    def _merge_window_panes(
        self,
        pane_plans: tuple[_PanePlan, ...],
        *,
        window_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        window_id: str,
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
                    window_id=window_id,
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
        layers = list(inherited_layers)
        if include_callsite_layer:
            layers.append(self._node_layer(callsite))
        layers.append(self._node_layer(target))
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
        window_id: str,
        window_attributes: dict[str, str],
    ) -> PaneSpec:
        base_scope = self._resolve_scope(
            window_layers,
            strict=False,
            subject=f'Implicit pane in window "{window_name}"',
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
        )

        if pane_plan.expands_window_root:
            steps = self._expand_node(
                pane_plan.source_window,
                inherited_layers=window_layers,
                session_name=session_name,
                window_name=window_name,
                window_id=window_id,
                window_attributes=window_attributes,
                pane_name=None,
                pane_id=None,
                pane_attributes=None,
                relationship_stack=(),
                include_local_layer=False,
                allow_text_payload=False,
                args_namespace=None,
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
                        window_id=window_id,
                        window_attributes=window_attributes,
                        pane_name=None,
                        pane_id=None,
                        pane_attributes=None,
                        relationship_stack=(),
                        args_namespace=None,
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
        window_id: str,
        window_attributes: dict[str, str],
    ) -> PaneSpec:
        children = self._non_alias_children(pane_root)
        structural_root = bool(children and not pane_root.detail and not pane_root.relationships)
        pane_attributes = self._runtime_attribute_dict(pane_root)
        base_layers = self._layers_for_node(window_layers, pane_root, include_local_layer=True)
        title = self._render_pane_title(
            pane_root,
            window_layers=window_layers,
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
        )

        base_scope = self._resolve_scope(
            base_layers,
            strict=False,
            subject=f'Pane root {pane_root.id} "{pane_root.text}"',
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
            pane_name=title,
            pane_id=pane_root.id,
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
                        window_id=window_id,
                        window_attributes=window_attributes,
                        pane_name=title,
                        pane_id=pane_root.id,
                        pane_attributes=pane_attributes,
                        relationship_stack=(),
                        args_namespace=None,
                    )
                )
        else:
            steps = self._expand_node(
                pane_root,
                inherited_layers=window_layers,
                session_name=session_name,
                window_name=window_name,
                window_id=window_id,
                window_attributes=window_attributes,
                pane_name=title,
                pane_id=pane_root.id,
                pane_attributes=pane_attributes,
                relationship_stack=(),
                allow_text_payload=False,
                args_namespace=None,
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
        window_id: str,
        window_attributes: dict[str, str],
        pane_name: str | None,
        pane_id: str | None,
        pane_attributes: dict[str, str] | None,
        relationship_stack: tuple[str, ...],
        args_namespace: dict[str, str] | None,
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
        relationship_args = dict(args_namespace or {})
        relationship_args.update(self._relationship_target_defaults(target))
        relationship_args.update(self._relationship_callsite_overrides(callsite))
        return self._expand_node(
            target,
            inherited_layers=relationship_layers,
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
            pane_name=pane_name,
            pane_id=pane_id,
            pane_attributes=pane_attributes,
            relationship_stack=(*relationship_stack, target.id),
            include_local_layer=False,
            function_root=True,
            args_namespace=relationship_args,
        )

    def _expand_node(
        self,
        node: RawNode,
        *,
        inherited_layers: tuple[ScopeLayer, ...],
        session_name: str,
        window_name: str,
        window_id: str,
        window_attributes: dict[str, str],
        pane_name: str | None,
        pane_id: str | None,
        pane_attributes: dict[str, str] | None,
        relationship_stack: tuple[str, ...],
        include_local_layer: bool = True,
        function_root: bool = False,
        allow_text_payload: bool = True,
        args_namespace: dict[str, str] | None = None,
    ) -> list[CommandStep]:
        targets = self._helper_relationship_targets(node)
        local_layers = self._layers_for_node(
            inherited_layers, node, include_local_layer=include_local_layer
        )
        local_bindings = self._local_template_bindings(node) if include_local_layer else None
        node_attributes = self._runtime_attribute_dict(node)
        node_name = self._render_node_text(
            node.text,
            local_layers,
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
            pane_name=pane_name,
            pane_id=pane_id,
            pane_attributes=pane_attributes,
            node_id=node.id,
            node_attributes=node_attributes,
            subject=self._field_subject(node, "text"),
            args_namespace=args_namespace,
            local_bindings=local_bindings,
        )
        children = self._non_alias_children(node)
        uses_text_payload = bool(
            allow_text_payload
            and node.text.strip()
            and not node.detail
            and not node.relationships
            and (not function_root or not children)
        )
        has_direct_payload = bool(node.detail and node.detail.strip()) or uses_text_payload
        scope = self._resolve_scope(
            local_layers,
            strict=has_direct_payload,
            subject=f'Node {node.id} "{node.text}"',
            session_name=session_name,
            window_name=window_name,
            window_id=window_id,
            window_attributes=window_attributes,
            pane_name=pane_name,
            pane_id=pane_id,
            pane_attributes=pane_attributes,
            node_name=node_name,
            node_id=node.id,
            node_attributes=node_attributes,
            args_namespace=args_namespace,
            local_bindings=local_bindings,
        )

        steps: list[CommandStep] = []

        if node.detail and node.detail.strip():
            steps.extend(
                self._command_steps(
                    node,
                    template=node.detail,
                    payload_source="detail",
                    scope=scope,
                    display_name=node_name,
                )
            )
        elif uses_text_payload:
            steps.extend(
                self._command_steps(
                    node,
                    template=node.text,
                    payload_source="text",
                    scope=scope,
                    display_name=node_name,
                )
            )

        for target in targets:
            steps.extend(
                self._expand_relationship_target(
                    target,
                    inherited_layers=local_layers,
                    callsite=node,
                    include_callsite_layer=False,
                    session_name=session_name,
                    window_name=window_name,
                    window_id=window_id,
                    window_attributes=window_attributes,
                    pane_name=pane_name,
                    pane_id=pane_id,
                    pane_attributes=pane_attributes,
                    relationship_stack=relationship_stack,
                    args_namespace=args_namespace,
                )
            )

        for child in children:
            steps.extend(
                self._expand_node(
                    child,
                    inherited_layers=local_layers,
                    session_name=session_name,
                    window_name=window_name,
                    window_id=window_id,
                    window_attributes=window_attributes,
                    pane_name=pane_name,
                    pane_id=pane_id,
                    pane_attributes=pane_attributes,
                    relationship_stack=relationship_stack,
                    args_namespace=args_namespace,
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
        display_name: str,
    ) -> list[CommandStep]:
        field_name = "detail" if payload_source == "detail" else "text"
        commands = self._resolver.render_command(
            template,
            scope,
            subject=self._field_subject(node, field_name),
        )
        return [
            CommandStep(
                node_id=node.id,
                display_name=display_name,
                payload_source=payload_source,
                command=command,
                effective_scope=scope,
            )
            for command in commands
        ]
