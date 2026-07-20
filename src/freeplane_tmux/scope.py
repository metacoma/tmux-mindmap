from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from typing import Any

from .errors import SemanticError
from .models import AliasTemplate, RawNode, ScopeLayer, ScopeSnapshot
from .templates import (
    TEMPLATE_RE,
    ShellList,
    render_template,
    require_resolved,
    stringify_shell_value,
    stringify_template_value,
)
from .text import split_shell_commands

ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def split_attributes(attributes: dict[str, Any]) -> ScopeLayer:
    vars_out: dict[str, str] = {}
    env_out: dict[str, str] = {}
    pre_out: list[str] = []

    for key, raw_value in attributes.items():
        value = to_string(raw_value)
        if key == "pre":
            pre_out.extend(split_shell_commands(value))
        elif ENV_NAME_RE.fullmatch(key):
            env_out[key] = value
        else:
            vars_out[key] = value

    return ScopeLayer(vars=vars_out, env=env_out, pre=tuple(pre_out))


def combine_layer(base: ScopeLayer, *, aliases: dict[str, AliasTemplate]) -> ScopeLayer:
    return ScopeLayer(vars=base.vars, env=base.env, pre=base.pre, aliases=aliases)


class ScopeResolver:
    """Resolve a path-scoped stack at the exact command call site."""

    def __init__(self, root_node: RawNode | None = None):
        self.root_node = root_node

    @staticmethod
    def _parse_detail_list(value: str) -> tuple[str, ...] | None:
        text = value.strip()
        if not text or "\n" in text:
            return None
        try:
            parts = tuple(shlex.split(text, posix=True))
        except ValueError:
            return None
        if len(parts) <= 1:
            return None
        return parts

    def resolve(
        self,
        layers: Sequence[ScopeLayer],
        *,
        builtins: dict[str, str],
        strict: bool,
        subject: str,
    ) -> ScopeSnapshot:
        raw_vars: dict[str, str] = {}
        raw_env: dict[str, str] = {}
        raw_pre: list[str] = []
        raw_aliases: dict[str, AliasTemplate] = {}

        for layer in layers:
            raw_vars.update(layer.vars)
            raw_env.update(layer.env)
            raw_pre.extend(layer.pre)
            raw_aliases.update(layer.aliases)

        namespace_raw: dict[str, str] = {**raw_vars, **raw_env, **builtins}
        resolved_namespace: dict[str, str] = {}
        resolving: list[str] = []
        node_value_cache: dict[str, str | ShellList] = {}
        node_value_resolving: list[str] = []

        def resolve_node_value(node: RawNode) -> str | ShellList:
            cached = node_value_cache.get(node.id)
            if cached is not None:
                return cached
            if node.id in node_value_resolving:
                cycle = " -> ".join([*node_value_resolving, node.id])
                raise SemanticError(f"cyclic root template reference: {cycle}")

            node_value_resolving.append(node.id)
            try:
                if node.children:
                    items: list[str] = []
                    for child in node.children:
                        child_value = resolve_node_value(child)
                        if isinstance(child_value, ShellList):
                            items.extend(child_value.items)
                        else:
                            items.append(child_value)
                    result: str | ShellList = ShellList(tuple(items))
                elif node.detail is not None and node.detail.strip():
                    rendered_detail = render_template(
                        node.detail,
                        lookup_value,
                        stringify=stringify_template_value,
                    )
                    parsed_list = self._parse_detail_list(rendered_detail)
                    result = ShellList(parsed_list) if parsed_list is not None else rendered_detail
                else:
                    result = render_template(
                        node.text,
                        lookup_value,
                        stringify=stringify_template_value,
                    )
            finally:
                node_value_resolving.pop()

            node_value_cache[node.id] = result
            return result

        def resolve_root_key(key: str) -> str | ShellList | None:
            if self.root_node is None or not (key == "root" or key.startswith("root.")):
                return None
            if key == "root":
                return resolve_node_value(self.root_node)

            current = self.root_node
            for segment in key.split(".")[1:]:
                match = next((child for child in current.children if child.text == segment), None)
                if match is None:
                    return None
                current = match
            return resolve_node_value(current)

        def lookup_value(key: str) -> str | ShellList | None:
            root_value = resolve_root_key(key)
            if root_value is not None:
                return root_value
            if key in resolved_namespace:
                return resolved_namespace[key]
            if key not in namespace_raw:
                return None
            if key in resolving:
                cycle = " -> ".join([*resolving, key])
                raise SemanticError(f"cyclic template reference: {cycle}")

            resolving.append(key)
            rendered = render_template(
                namespace_raw[key],
                lookup_value,
                stringify=stringify_template_value,
            )
            resolving.pop()
            resolved_namespace[key] = rendered
            return rendered

        for key in namespace_raw:
            value = lookup_value(key)
            if isinstance(value, str):
                resolved_namespace[key] = value

        vars_out = {
            key: value
            for key in (*raw_vars.keys(), *builtins.keys())
            if (value := resolved_namespace.get(key)) is not None and not TEMPLATE_RE.search(value)
        }
        env_out = {
            key: value
            for key in raw_env
            if (value := resolved_namespace.get(key)) is not None and not TEMPLATE_RE.search(value)
        }

        def lookup(key: str) -> str | ShellList | None:
            root_value = resolve_root_key(key)
            if root_value is not None:
                return root_value
            value = resolved_namespace.get(key)
            if value is None or TEMPLATE_RE.search(value):
                return None
            return value

        pre_out: list[str] = []
        for index, command_template in enumerate(raw_pre):
            rendered = render_template(
                command_template,
                lookup,
                stringify=stringify_shell_value,
            )
            if TEMPLATE_RE.search(rendered):
                if strict:
                    require_resolved(rendered, subject=f"pre command {index + 1} for {subject}")
                continue
            pre_out.extend(split_shell_commands(rendered))

        aliases_out: dict[str, str] = {}
        for name, alias_template in raw_aliases.items():
            rendered_lines: list[str] = []
            unresolved = False
            for command_template in alias_template.command_templates:
                rendered = render_template(
                    command_template,
                    lookup,
                    stringify=stringify_shell_value,
                )
                if TEMPLATE_RE.search(rendered):
                    unresolved = True
                    if strict:
                        require_resolved(
                            rendered,
                            subject=(
                                f"alias {name!r} from node {alias_template.source_node_id!r} "
                                f"for {subject}"
                            ),
                        )
                    break
                rendered_lines.extend(split_shell_commands(rendered))
            if not unresolved and rendered_lines:
                aliases_out[name] = "; ".join(rendered_lines)

        return ScopeSnapshot(
            vars=vars_out,
            env=env_out,
            pre=tuple(pre_out),
            aliases=aliases_out,
            root_lookup=resolve_root_key,
        )

    def render_value(self, template: str, scope: ScopeSnapshot, *, subject: str) -> str:
        rendered = render_template(template, scope.lookup, stringify=stringify_template_value)
        return require_resolved(rendered, subject=subject)

    def render_command(self, template: str, scope: ScopeSnapshot, *, subject: str) -> list[str]:
        rendered = render_template(template, scope.lookup, stringify=stringify_shell_value)
        rendered = require_resolved(rendered, subject=subject)
        return split_shell_commands(rendered)
