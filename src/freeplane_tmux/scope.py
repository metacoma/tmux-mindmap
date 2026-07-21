from __future__ import annotations

import re
import shlex
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .errors import SemanticError
from .models import AliasTemplate, RawNode, ScopeLayer, ScopeSnapshot
from .templates import TEMPLATE_RE, ShellList
from .text import join_shell_commands, sanitize_details_text, split_shell_commands

_TEMPLATE_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_SCOPED_NAMES = {
    "args",
    "env",
    "false",
    "node",
    "none",
    "pane",
    "root",
    "scoped",
    "script1",
    "session",
    "true",
    "vars",
    "window",
}
_SERVICE_ATTRIBUTE_NAMES = {
    "exec.pre",
    "exec.pre_mode",
    "exec.workdir",
    "script1",
    "tmux.layout",
    "tmux.mode",
}
_RESERVED_LOCAL_BINDING_NAMES = {
    "args",
    "env",
    "node",
    "pane",
    "session",
    "vars",
    "window",
}


@dataclass(frozen=True)
class RawScalarTemplate:
    path: str
    template: str


@dataclass(frozen=True)
class RawListTemplate:
    path: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class CompiledVarsNamespace:
    scalars: dict[str, RawScalarTemplate]
    lists: dict[str, RawListTemplate]
    object_fields: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class RuntimeTemplateContext:
    scalars: dict[str, str]
    object_fields: dict[str, tuple[str, ...]]


def to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _validate_template_segment(segment: str, *, kind: str, path: str) -> None:
    if not _TEMPLATE_SEGMENT_RE.fullmatch(segment):
        raise SemanticError(
            f"invalid {kind} name {segment!r} at {path}; use [A-Za-z_][A-Za-z0-9_]*"
        )


def _validate_env_name(name: str) -> None:
    if not _ENV_NAME_RE.fullmatch(name):
        raise SemanticError(
            f"invalid environment variable name {name!r}; use [A-Za-z_][A-Za-z0-9_]*"
        )


def _merge_object_fields(*mappings: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    merged: dict[str, OrderedDict[str, None]] = {}
    for mapping in mappings:
        for path, fields in mapping.items():
            bucket = merged.setdefault(path, OrderedDict())
            for field in fields:
                bucket.setdefault(field, None)
    return {path: tuple(fields.keys()) for path, fields in merged.items()}


def split_attributes(
    attributes: dict[str, Any], *, node_id: str | None = None, node_text: str | None = None
) -> ScopeLayer:
    scoped_vars: dict[str, str] = {}
    env_out: dict[str, str] = {}
    pre_out: list[str] = []
    runtime_attrs: dict[str, str] = {}
    call_args: dict[str, str] = {}
    helper_defaults: dict[str, str] = {}
    tmux_mode: str | None = None
    tmux_layout: str | None = None

    for key, raw_value in attributes.items():
        value = to_string(raw_value)
        if key == "script1":
            continue
        if key == "exec.pre":
            pre_out.extend(split_shell_commands(value))
            continue
        if key == "exec.pre_mode":
            if value and value != "append":
                raise SemanticError(
                    f"unsupported exec.pre_mode {value!r}; only append semantics are supported"
                )
            continue
        if key == "exec.workdir":
            continue
        if key == "tmux.mode":
            tmux_mode = value or None
            continue
        if key == "tmux.layout":
            tmux_layout = value or None
            continue
        if key.startswith("var."):
            name = key.removeprefix("var.")
            _validate_template_segment(name, kind="scoped variable", path=key)
            if name in _RESERVED_SCOPED_NAMES:
                raise SemanticError(f'Scoped variable name "{name}" is reserved')
            scoped_vars[name] = value
            continue
        if key.startswith("env."):
            name = key.removeprefix("env.")
            _validate_env_name(name)
            env_out[name] = value
            continue
        if key.startswith("arg."):
            name = key.removeprefix("arg.")
            _validate_template_segment(name, kind="argument", path=key)
            call_args[name] = value
            continue
        if key.startswith("default."):
            name = key.removeprefix("default.")
            _validate_template_segment(name, kind="default argument", path=key)
            helper_defaults[name] = value
            continue
        if key == "type" and value == "list":
            continue
        _validate_template_segment(key, kind="attribute", path=key)
        if key in _RESERVED_LOCAL_BINDING_NAMES:
            node_label = node_id if node_text is None else f'{node_id} "{node_text}"'
            raise SemanticError(f'ordinary attribute name "{key}" is reserved in node {node_label}')
        runtime_attrs[key] = value

    return ScopeLayer(
        scoped_vars=scoped_vars,
        env=env_out,
        pre=tuple(pre_out),
        runtime_attrs=runtime_attrs,
        call_args=call_args,
        helper_defaults=helper_defaults,
        tmux_mode=tmux_mode,
        tmux_layout=tmux_layout,
    )


def combine_layer(base: ScopeLayer, *, aliases: dict[str, AliasTemplate]) -> ScopeLayer:
    return ScopeLayer(
        scoped_vars=base.scoped_vars,
        env=base.env,
        pre=base.pre,
        aliases=aliases,
        runtime_attrs=base.runtime_attrs,
        call_args=base.call_args,
        helper_defaults=base.helper_defaults,
        tmux_mode=base.tmux_mode,
        tmux_layout=base.tmux_layout,
    )


class _TemplateLookupError(SemanticError):
    pass


class ScopeResolver:
    """Resolve a path-scoped stack at the exact command call site."""

    def __init__(self, compiled_vars: CompiledVarsNamespace):
        self.compiled_vars = compiled_vars

    def resolve(
        self,
        layers: Sequence[ScopeLayer],
        *,
        runtime_context: RuntimeTemplateContext,
        args_namespace: dict[str, str] | None,
        local_bindings: dict[str, str] | None,
        strict: bool,
        subject: str,
    ) -> ScopeSnapshot:
        raw_scoped: dict[str, str] = {}
        raw_runtime_attrs: dict[str, str] = {}
        raw_env: dict[str, str] = {}
        raw_pre: list[str] = []
        raw_aliases: dict[str, AliasTemplate] = {}

        for layer in layers:
            raw_scoped.update(layer.scoped_vars)
            raw_runtime_attrs.update(layer.runtime_attrs)
            raw_env.update(layer.env)
            raw_pre.extend(layer.pre)
            raw_aliases.update(layer.aliases)

        raw_scalars: dict[str, RawScalarTemplate] = dict(self.compiled_vars.scalars)
        raw_lists: dict[str, RawListTemplate] = dict(self.compiled_vars.lists)
        object_fields = _merge_object_fields(
            self.compiled_vars.object_fields, runtime_context.object_fields
        )

        for name, value in raw_scoped.items():
            raw_scalars[name] = RawScalarTemplate(path=name, template=value)
        for name, value in raw_runtime_attrs.items():
            raw_scalars[name] = RawScalarTemplate(path=name, template=value)
        if raw_env:
            object_fields = _merge_object_fields(object_fields, {"env": tuple(raw_env.keys())})
            for name, value in raw_env.items():
                raw_scalars[f"env.{name}"] = RawScalarTemplate(path=f"env.{name}", template=value)
        if args_namespace:
            object_fields = _merge_object_fields(
                object_fields, {"args": tuple(args_namespace.keys())}
            )
            for name, value in args_namespace.items():
                raw_scalars[f"args.{name}"] = RawScalarTemplate(path=f"args.{name}", template=value)
        else:
            object_fields = _merge_object_fields(object_fields, {"args": ()})

        if args_namespace:
            for name, value in args_namespace.items():
                raw_scalars[name] = RawScalarTemplate(path=name, template=value)

        if local_bindings:
            for name, value in local_bindings.items():
                raw_scalars[name] = RawScalarTemplate(path=name, template=value)

        for path, value in runtime_context.scalars.items():
            raw_scalars[path] = RawScalarTemplate(path=path, template=value)

        resolved_scalars: dict[str, str] = {}
        resolved_lists: dict[str, tuple[str, ...]] = {}
        resolving: list[str] = []

        def describe_available(path: str) -> str:
            fields = object_fields.get(path, ())
            if not fields:
                return f"No fields are available under {path}."
            joined = ", ".join(fields)
            return f"Available fields under {path}: {joined}"

        def missing_key_error(path: str) -> _TemplateLookupError:
            parts = path.split(".")
            prefix = parts[0]
            if (
                prefix not in object_fields
                and prefix not in raw_scalars
                and prefix not in raw_lists
            ):
                top_level = sorted(
                    {
                        *object_fields.keys(),
                        *(name for name in raw_scalars if "." not in name),
                        *(name for name in raw_lists if "." not in name),
                    }
                )
                joined = ", ".join(top_level)
                message = (
                    f'{subject}: undefined template variable "{path}". '
                    f"Available top-level names: {joined}"
                )
                return _TemplateLookupError(message)

            current = prefix
            for segment in parts[1:]:
                if current in raw_scalars or current in raw_lists:
                    return _TemplateLookupError(
                        f'{subject}: undefined template variable "{path}". '
                        f"Path prefix {current} is not an object value."
                    )
                fields = object_fields.get(current)
                if fields is None:
                    break
                if segment not in fields:
                    return _TemplateLookupError(
                        f'{subject}: undefined template variable "{path}". '
                        f"{describe_available(current)}"
                    )
                current = f"{current}.{segment}"
            return _TemplateLookupError(f'{subject}: undefined template variable "{path}".')

        def resolve_path(path: str) -> str | ShellList:
            if path in resolved_scalars:
                return resolved_scalars[path]
            if path in resolved_lists:
                return ShellList(resolved_lists[path])
            if path in object_fields and path not in raw_scalars and path not in raw_lists:
                raise _TemplateLookupError(
                    f"{subject}: Cannot render object {path} as a scalar value. "
                    f"{describe_available(path)}"
                )
            if path in resolving:
                cycle = " -> ".join([*resolving, path])
                raise SemanticError(f"cyclic template reference: {cycle}")
            if path in raw_scalars:
                resolving.append(path)
                try:
                    rendered = self._render_template(
                        raw_scalars[path].template,
                        resolve_path,
                        stringify=stringify_template_value,
                    )
                finally:
                    resolving.pop()
                resolved_scalars[path] = rendered
                return rendered
            if path in raw_lists:
                resolving.append(path)
                try:
                    items = tuple(
                        self._render_template(
                            item, resolve_path, stringify=stringify_template_value
                        )
                        for item in raw_lists[path].items
                    )
                finally:
                    resolving.pop()
                resolved_lists[path] = items
                return ShellList(items)
            raise missing_key_error(path)

        env_out = {
            name: self._render_template(template, resolve_path, stringify=stringify_template_value)
            for name, template in raw_env.items()
        }

        def late_lookup(path: str) -> str | ShellList:
            value = resolve_path(path)
            if isinstance(value, ShellList):
                return value
            return value

        pre_out: list[str] = []
        for _index, command_template in enumerate(raw_pre):
            try:
                rendered = self._render_template(
                    command_template,
                    late_lookup,
                    stringify=stringify_shell_value,
                )
            except _TemplateLookupError as exc:
                if not strict:
                    continue
                raise SemanticError(str(exc)) from None
            pre_out.extend(split_shell_commands(rendered))

        aliases_out: dict[str, str] = {}
        for name, alias_template in raw_aliases.items():
            rendered_lines: list[str] = []
            unresolved = False
            for command_template in alias_template.command_templates:
                try:
                    rendered = self._render_template(
                        command_template,
                        late_lookup,
                        stringify=stringify_shell_value,
                    )
                except _TemplateLookupError as exc:
                    unresolved = True
                    if strict:
                        raise SemanticError(str(exc)) from None
                    break
                rendered_lines.extend(split_shell_commands(rendered))
            if not unresolved and rendered_lines:
                aliases_out[name] = join_shell_commands(rendered_lines)

        visible_scalars = dict(resolved_scalars)
        visible_scalars.update({f"env.{name}": value for name, value in env_out.items()})

        return ScopeSnapshot(
            vars=visible_scalars,
            lists=dict(resolved_lists),
            object_fields=object_fields,
            env=env_out,
            pre=tuple(pre_out),
            aliases=aliases_out,
            lookup_value=resolve_path,
        )

    def render_value(self, template: str, scope: ScopeSnapshot, *, subject: str) -> str:
        return self._render_template(
            template,
            lambda path: self._resolve_from_snapshot(path, scope, subject=subject),
            stringify=stringify_template_value,
        )

    def render_command_block(self, template: str, scope: ScopeSnapshot, *, subject: str) -> str:
        cleaned = sanitize_details_text(template)
        return self._render_template(
            cleaned,
            lambda path: self._resolve_from_snapshot(path, scope, subject=subject),
            stringify=stringify_shell_value,
        )

    def render_command(self, template: str, scope: ScopeSnapshot, *, subject: str) -> list[str]:
        rendered = self.render_command_block(template, scope, subject=subject)
        return split_shell_commands(rendered)

    @staticmethod
    def _render_template(
        value: str,
        lookup: Any,
        *,
        max_passes: int = 64,
        stringify: Any,
    ) -> str:
        rendered = value
        for _ in range(max_passes):
            changed = False

            def replace(match: re.Match[str]) -> str:
                nonlocal changed
                key = match.group(1).strip()
                replacement = lookup(key)
                replacement_text = stringify(replacement)
                if replacement_text != match.group(0):
                    changed = True
                return replacement_text

            rendered = TEMPLATE_RE.sub(replace, rendered)
            if not changed:
                return rendered
        raise SemanticError(f"template exceeded {max_passes} render passes: {value!r}")

    def _resolve_from_snapshot(
        self,
        path: str,
        scope: ScopeSnapshot,
        *,
        subject: str,
    ) -> str | ShellList:
        if path in scope.vars:
            return scope.vars[path]
        if path in scope.lists:
            return ShellList(scope.lists[path])
        if scope.lookup_value is not None:
            value = scope.lookup_value(path)
            if value is not None:
                if isinstance(value, tuple):
                    return ShellList(value)
                return value
        if path in scope.object_fields:
            fields = ", ".join(scope.object_fields[path])
            message = (
                f"{subject}: Cannot render object {path} as a scalar value. "
                f"Available fields: {fields}"
            )
            raise SemanticError(message)
        raise SemanticError(f'{subject}: undefined template variable "{path}"')


def stringify_template_value(value: Any) -> str:
    if isinstance(value, ShellList):
        return " ".join(value.items)
    if isinstance(value, tuple):
        return " ".join(value)
    return str(value)


def stringify_shell_value(value: Any) -> str:
    if isinstance(value, ShellList):
        return " ".join(shlex.quote(item) for item in value.items)
    if isinstance(value, tuple):
        return " ".join(shlex.quote(item) for item in value)
    return str(value)


def compile_vars_namespace(root: RawNode) -> CompiledVarsNamespace:
    vars_nodes = [child for child in root.children if child.text == "vars"]
    if not vars_nodes:
        return CompiledVarsNamespace(scalars={}, lists={}, object_fields={})
    if len(vars_nodes) > 1:
        raise SemanticError("multiple root children named 'vars' are not allowed")

    scalars: dict[str, RawScalarTemplate] = {}
    lists: dict[str, RawListTemplate] = {}
    object_fields: dict[str, tuple[str, ...]] = {}

    def compile_object(node: RawNode, path: str) -> None:
        explicit_list = "LIST" in node.tags or node.attributes.get("type") == "list"
        if explicit_list:
            compile_list(node, path)
            return

        user_attrs = OrderedDict()
        for key, raw_value in node.attributes.items():
            if key in {"type", *sorted(_SERVICE_ATTRIBUTE_NAMES)}:
                continue
            _validate_template_segment(key, kind="vars attribute", path=f"{path}.{key}")
            user_attrs[key] = to_string(raw_value)

        children_by_name: OrderedDict[str, RawNode] = OrderedDict()
        for child in node.children:
            child_name = child.text
            _validate_template_segment(child_name, kind="vars field", path=f"{path}.{child_name}")
            if child_name in children_by_name:
                raise SemanticError(f"Duplicate variable path: {path}.{child_name}")
            children_by_name[child_name] = child

        conflicts = sorted(set(user_attrs).intersection(children_by_name))
        if conflicts:
            conflict_path = f"{path}.{conflicts[0]}"
            message = (
                f"Duplicate variable path: {conflict_path}. "
                "Defined both as an attribute and as a child node"
            )
            raise SemanticError(message)

        if not user_attrs and not children_by_name:
            if node.detail is None or not sanitize_details_text(node.detail).strip():
                raise SemanticError(f"Variable {path} has no value")
            scalars[path] = RawScalarTemplate(
                path=path, template=sanitize_details_text(node.detail)
            )
            return

        object_fields[path] = tuple([*user_attrs.keys(), *children_by_name.keys()])
        for key, value in user_attrs.items():
            child_path = f"{path}.{key}"
            scalars[child_path] = RawScalarTemplate(path=child_path, template=value)
        for child_name, child in children_by_name.items():
            compile_object(child, f"{path}.{child_name}")

    def compile_list(node: RawNode, path: str) -> None:
        if node.detail and sanitize_details_text(node.detail).strip():
            raise SemanticError(f"Explicit list {path} must use child items, not detail text")
        items: list[str] = []
        for child in node.children:
            if child.children:
                raise SemanticError(
                    f"Explicit list {path} cannot contain nested objects at item {child.id!r}"
                )
            item_value = sanitize_details_text(child.detail) if child.detail else child.text
            if not item_value.strip():
                raise SemanticError(
                    f"Explicit list {path} contains an empty item at node {child.id!r}"
                )
            items.append(item_value)
        lists[path] = RawListTemplate(path=path, items=tuple(items))

    compile_object(vars_nodes[0], "vars")
    if "vars" not in object_fields:
        object_fields["vars"] = ()
    return CompiledVarsNamespace(scalars=scalars, lists=lists, object_fields=object_fields)
