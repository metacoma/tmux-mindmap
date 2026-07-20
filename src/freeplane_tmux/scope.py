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
    TemplateValue,
    render_template,
    require_resolved,
    shellify_template_value,
    stringify_template_value,
)
from .text import sanitize_details_text, split_shell_commands

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


def _detail_to_shell_words(value: str) -> tuple[str, ...]:
    try:
        words = tuple(shlex.split(value, posix=True))
    except ValueError:
        words = ()
    if words:
        return words
    return (value,)


def _node_template_value(node: RawNode) -> TemplateValue:
    if node.children:
        child_values = [_node_template_value(child) for child in node.children]
        items: list[str] = []
        for value in child_values:
            if isinstance(value, ShellList):
                items.extend(value.items)
            else:
                items.append(value)
        child_text = " ".join(stringify_template_value(value) for value in child_values).strip()
        return ShellList(
            items=tuple(items),
            text=child_text or None,
        )

    detail = sanitize_details_text(node.detail)
    if detail.strip():
        return ShellList(items=_detail_to_shell_words(detail), text=detail)

    return node.text


def _root_tree_lookup(root_node: RawNode | None, key: str) -> TemplateValue | None:
    if root_node is None or key != "root" and not key.startswith("root."):
        return None

    current = root_node
    for segment in key.split(".")[1:]:
        current = next((child for child in current.children if child.text == segment), None)
        if current is None:
            return None

    return _node_template_value(current)


class ScopeResolver:
    """Resolve a path-scoped stack at the exact command call site."""

    def resolve(
        self,
        layers: Sequence[ScopeLayer],
        *,
        builtins: dict[str, str],
        strict: bool,
        subject: str,
        root_node: RawNode | None = None,
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

        def resolve_key(key: str) -> str | None:
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
                lookup,
                formatter=stringify_template_value,
            )
            resolving.pop()
            resolved_namespace[key] = rendered
            return rendered

        def lookup(key: str) -> TemplateValue | None:
            root_value = _root_tree_lookup(root_node, key)
            if root_value is not None:
                return root_value
            value = resolve_key(key)
            if value is not None:
                if TEMPLATE_RE.search(value):
                    return None
                return value
            return None

        for key in namespace_raw:
            resolve_key(key)

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

        pre_out: list[str] = []
        for index, command_template in enumerate(raw_pre):
            rendered = render_template(command_template, lookup, formatter=shellify_template_value)
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
                    formatter=shellify_template_value,
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
            root_lookup=(
                (lambda key: _root_tree_lookup(root_node, key)) if root_node is not None else None
            ),
        )

    def render_value(self, template: str, scope: ScopeSnapshot, *, subject: str) -> str:
        rendered = render_template(template, scope.lookup, formatter=stringify_template_value)
        return require_resolved(rendered, subject=subject)

    def render_command(self, template: str, scope: ScopeSnapshot, *, subject: str) -> list[str]:
        rendered = render_template(template, scope.lookup, formatter=shellify_template_value)
        return split_shell_commands(require_resolved(rendered, subject=subject))
