from __future__ import annotations

import json
from pathlib import Path

import yaml
from conftest import compile_map, node

from freeplane_tmux.diagnostics import build_explain_plan
from freeplane_tmux.models import RawNode

FIXTURE_DIR = Path(__file__).parents[1] / "examples" / "history"


def test_relationship_plain_attributes_use_defaults_and_callsite_overrides() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node(
                        "connect",
                        "connect",
                        relationship="mongo-helper",
                        attributes={"user": "root"},
                    )
                ],
            ),
            node(
                "mongo-helper",
                "mongo helper",
                attributes={"user": "bebebeka"},
                detail="who | grep {{ user }}",
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["who | grep root"]


def test_relationship_without_target_defaults_uses_callsite_plain_attributes() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node(
                        "print-six",
                        "print six",
                        relationship="sum",
                        attributes={"first": "3", "second": "3"},
                    )
                ],
            ),
            node("sum", "sum", detail="echo {{ first }} + {{ second }} | bc"),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["echo 3 + 3 | bc"]


def test_dual_use_node_works_directly_and_through_relationship() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node(
                        "find-user",
                        "find user",
                        detail="who | grep {{ user }}",
                        attributes={"user": "bebebeka"},
                    ),
                    node(
                        "find-another",
                        "find another user",
                        relationship="find-user",
                        attributes={"user": "root"},
                    ),
                ],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["who | grep bebebeka", "who | grep root"]


def test_nested_relationship_uses_inherited_bindings_then_defaults_then_overrides() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node(
                        "outer-call",
                        "outer call",
                        relationship="outer-helper",
                        attributes={"user": "root", "role": "reader"},
                    )
                ],
            ),
            node(
                "outer-helper",
                "outer helper",
                children=[
                    node(
                        "inner-call",
                        "inner call",
                        relationship="inner-helper",
                        attributes={"role": "writer"},
                    )
                ],
            ),
            node(
                "inner-helper",
                "inner helper",
                attributes={"user": "bebebeka", "role": "admin"},
                detail="echo {{ user }} {{ role }}",
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["echo bebebeka writer"]


def test_relationship_exec_pre_appends_after_callsite_chain() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"exec.pre": "echo root"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"exec.pre": "echo window", "tmux.mode": "single-pane"},
                children=[
                    node(
                        "pane",
                        "db",
                        attributes={"exec.pre": "echo pane"},
                        children=[
                            node(
                                "deploy",
                                "deploy",
                                attributes={"exec.pre": "echo current"},
                                relationship="helper",
                            )
                        ],
                    )
                ],
            ),
            node("helper", "helper", attributes={"exec.pre": "echo helper"}, detail="echo run"),
        ],
    )

    step = compile_map(raw).windows[0].panes[0].steps[-1]
    assert step.command == "echo run"
    assert step.effective_scope.pre == (
        "echo root",
        "echo window",
        "echo pane",
        "echo current",
        "echo helper",
    )


def test_window_inheritance_fixture_uses_new_runtime_context() -> None:
    raw = yaml.safe_load((FIXTURE_DIR / "window-inheritance.map.yaml").read_text(encoding="utf-8"))
    session = compile_map(raw)

    mcmp3 = next(window for window in session.windows if window.name == "mcmp3")
    assert [pane.title for pane in mcmp3.panes] == ["pane1", "pane2", "additional pane"]
    assert [step.command for step in mcmp3.panes[0].steps] == [
        "ping mcmp3.mgmt.example.invalid",
    ]
    assert mcmp3.panes[0].base_scope.pre == ("ssh hw0076",)


def test_full_plain_attribute_relationship_fixture() -> None:
    raw = json.loads(
        (FIXTURE_DIR / "relationship-plain-attributes.map.json").read_text(encoding="utf-8")
    )
    session = compile_map(raw)

    assert [window.name for window in session.windows] == ["user find"]
    panes = session.windows[0].panes
    assert [pane.title for pane in panes] == [None, "find another user", "print six"]
    assert [pane.base_scope.pre for pane in panes] == [
        ("ssh hw0076",),
        ("ssh hw0076",),
        ("ssh hw0076",),
    ]
    assert [[step.command for step in pane.steps] for pane in panes] == [
        ["who | grep bebebeka"],
        ["who | grep root"],
        ["echo 3 + 3 | bc"],
    ]

    plan = build_explain_plan(RawNode.model_validate(raw), session)
    commands = [
        command["command"]
        for window in plan["windows"]
        for pane in window["panes"]
        for command in pane["commands"]
    ]
    assert "who | grep bebebeka" in commands
    assert "who | grep root" in commands
    assert "echo 3 + 3 | bc" in commands
    assert any(edge["source_node_id"] == "ID_1914466011" for edge in plan["relationships"])
    assert any(edge["source_node_id"] == "ID_119864851" for edge in plan["relationships"])
    assert "script1" not in json.dumps(plan, ensure_ascii=False)
