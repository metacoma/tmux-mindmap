from __future__ import annotations

import re
from collections.abc import Callable

from .errors import SemanticError

TEMPLATE_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
Lookup = Callable[[str], str | None]


def render_template(value: str, lookup: Lookup, *, max_passes: int = 64) -> str:
    rendered = value
    for _ in range(max_passes):
        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            key = match.group(1).strip()
            replacement = lookup(key)
            if replacement is None:
                return match.group(0)
            replacement = str(replacement)
            if replacement != match.group(0):
                changed = True
            return replacement

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
        raise SemanticError(f"cannot resolve {subject}; unresolved template keys: {names}")
    return value
