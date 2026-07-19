from __future__ import annotations

from typing import Any

from freeplane_tmux.compiler import MindmapCompiler
from freeplane_tmux.models import RawNode, SessionSpec


def node(
    node_id: str,
    text: str = "",
    *,
    children: list[dict[str, Any]] | None = None,
    detail: str | None = None,
    tags: list[str] | None = None,
    relationship: str | None = None,
    relationships: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": node_id,
        "text": text,
        "children": children or [],
        "tags": tags or [],
        "attributes": attributes or {},
    }
    if detail is not None:
        result["detail"] = detail
    if relationship is not None and relationships is not None:
        raise ValueError("use either relationship or relationships")
    if relationship is not None:
        result["relationships"] = [{"target_id": relationship}]
    elif relationships is not None:
        result["relationships"] = [{"target_id": target_id} for target_id in relationships]
    return result


def compile_map(raw: dict[str, Any]) -> SessionSpec:
    return MindmapCompiler(RawNode.model_validate(raw)).compile()
