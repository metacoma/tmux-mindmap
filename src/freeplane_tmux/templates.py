from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import SemanticError

TEMPLATE_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
Lookup = Callable[[str], Any | None]
Stringify = Callable[[Any], str]


@dataclass(frozen=True)
class ShellList:
    items: tuple[str, ...]


def stringify_template_value(value: Any) -> str:
    if isinstance(value, ShellList):
        return " ".join(value.items)
    return str(value)


def stringify_shell_value(value: Any) -> str:
    if isinstance(value, ShellList):
        return " ".join(shlex.quote(item) for item in value.items)
    return str(value)


def render_template(
    value: str,
    lookup: Lookup,
    *,
    max_passes: int = 64,
    stringify: Stringify = stringify_template_value,
) -> str:
    rendered = value
    for _ in range(max_passes):
        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            key = match.group(1).strip()
            replacement = lookup(key)
            if replacement is None:
                return match.group(0)
            replacement_text = stringify(replacement)
            if replacement_text != match.group(0):
                changed = True
            return replacement_text

        rendered = TEMPLATE_RE.sub(replace, rendered)
        if not changed:
            break
    return rendered


def unresolved_keys(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(1).strip() for match in TEMPLATE_RE.finditer(value)))


def require_resolved(value: str, *, subject: str) -> str:
    missing = unresolved_keys(value)
    if missing:
        names = ", ".join(missing)
        legacy = [name for name in missing if name in {"window-name", "pane-name"}]
        legacy_hint = ""
        if legacy:
            replacements = ", ".join(
                "window.name" if name == "window-name" else "pane.name" for name in legacy
            )
            legacy_hint = f"; legacy builtins were removed, use {replacements}"
        raise SemanticError(
            f"cannot resolve {subject}; unresolved template keys: {names}{legacy_hint}"
        )
    return value
