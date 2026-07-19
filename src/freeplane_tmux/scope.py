from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .errors import SemanticError
from .models import AliasTemplate, ScopeLayer, ScopeSnapshot
from .templates import TEMPLATE_RE, render_template, require_resolved
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

        def resolve_key(key: str) -> str | None:
            if key in resolved_namespace:
                return resolved_namespace[key]
            if key not in namespace_raw:
                return None
            if key in resolving:
                cycle = " -> ".join([*resolving, key])
                raise SemanticError(f"cyclic template reference: {cycle}")

            resolving.append(key)
            rendered = render_template(namespace_raw[key], resolve_key)
            resolving.pop()
            resolved_namespace[key] = rendered
            return rendered

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

        def lookup(key: str) -> str | None:
            value = resolved_namespace.get(key)
            if value is None or TEMPLATE_RE.search(value):
                return None
            return value

        pre_out: list[str] = []
        for index, command_template in enumerate(raw_pre):
            rendered = render_template(command_template, lookup)
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
                rendered = render_template(command_template, lookup)
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
        )

    def render_value(self, template: str, scope: ScopeSnapshot, *, subject: str) -> str:
        rendered = render_template(template, scope.lookup)
        return require_resolved(rendered, subject=subject)

    def render_command(self, template: str, scope: ScopeSnapshot, *, subject: str) -> list[str]:
        return split_shell_commands(self.render_value(template, scope, subject=subject))
