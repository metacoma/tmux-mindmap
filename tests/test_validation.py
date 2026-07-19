from __future__ import annotations

import pytest
from pydantic import ValidationError

from freeplane_tmux.compiler import MindmapCompiler
from freeplane_tmux.errors import SemanticError
from freeplane_tmux.models import RawNode


def test_raw_model_requires_node_id() -> None:
    with pytest.raises(ValidationError):
        RawNode.model_validate({"text": "missing id"})


def test_compiler_rejects_duplicate_node_ids() -> None:
    root = RawNode.model_validate(
        {
            "id": "root",
            "text": "demo",
            "children": [
                {"id": "duplicate", "text": "one"},
                {"id": "duplicate", "text": "two"},
            ],
        }
    )

    with pytest.raises(SemanticError, match="duplicate node id"):
        MindmapCompiler(root).compile()
