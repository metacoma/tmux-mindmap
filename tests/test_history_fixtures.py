from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from freeplane_tmux.compiler import MindmapCompiler
from freeplane_tmux.emitter import session_to_tmuxp
from freeplane_tmux.models import RawNode

FIXTURE_DIR = Path(__file__).parents[1] / "examples" / "history"


def _cases() -> list[dict[str, str]]:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    return manifest["cases"]


def _load_map(case: dict[str, str]) -> dict:
    path = FIXTURE_DIR / case["map"]
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_history_map_matches_canonical_tmuxp(case: dict[str, str]) -> None:
    raw = _load_map(case)
    expected = yaml.safe_load((FIXTURE_DIR / case["tmuxp"]).read_text(encoding="utf-8"))

    session = MindmapCompiler(RawNode.model_validate(raw)).compile()
    actual = session_to_tmuxp(session)

    assert actual == expected


def test_history_manifest_covers_every_committed_pair() -> None:
    cases = _cases()
    manifest_maps = {case["map"] for case in cases}
    manifest_tmuxp = {case["tmuxp"] for case in cases}

    fixture_maps = {path.name for path in FIXTURE_DIR.glob("*.map.json")} | {
        path.name for path in FIXTURE_DIR.glob("*.map.yaml")
    }
    assert manifest_maps == fixture_maps
    assert manifest_tmuxp == {path.name for path in FIXTURE_DIR.glob("*.tmuxp.yaml")}
