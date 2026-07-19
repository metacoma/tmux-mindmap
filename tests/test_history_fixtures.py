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


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_history_map_matches_canonical_tmuxp(case: dict[str, str]) -> None:
    raw = json.loads((FIXTURE_DIR / case["map"]).read_text(encoding="utf-8"))
    expected = yaml.safe_load((FIXTURE_DIR / case["tmuxp"]).read_text(encoding="utf-8"))

    session = MindmapCompiler(RawNode.model_validate(raw)).compile()
    actual = session_to_tmuxp(session)

    assert actual == expected


def test_history_manifest_covers_every_committed_pair() -> None:
    cases = _cases()
    manifest_maps = {case["map"] for case in cases}
    manifest_tmuxp = {case["tmuxp"] for case in cases}

    assert manifest_maps == {path.name for path in FIXTURE_DIR.glob("*.map.json")}
    assert manifest_tmuxp == {path.name for path in FIXTURE_DIR.glob("*.tmuxp.yaml")}


def test_jinja_node_names_fixture_expands_window_and_pane_titles() -> None:
    raw = json.loads((FIXTURE_DIR / "jinja-node-names.map.json").read_text(encoding="utf-8"))

    session = MindmapCompiler(RawNode.model_validate(raw)).compile()

    assert [window.name for window in session.windows] == [
        "hello-win",
        "mcmp2",
        "mcmp3",
    ]
    assert [pane.title for pane in session.windows[1].panes] == ["ping", "mcmp2"]
    assert [step.command for step in session.windows[1].panes[1].steps] == [
        "ssh mcmp2",
        "hostname",
    ]
    assert [pane.title for pane in session.windows[2].panes] == ["ping", "mcmp3"]
    assert [step.command for step in session.windows[2].panes[1].steps] == [
        "ssh mcmp3",
        "hostname",
    ]
