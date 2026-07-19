from __future__ import annotations

import json
from pathlib import Path

from conftest import compile_map, node


def commands(session) -> list[str]:
    return [step.command for step in session.windows[0].panes[0].steps]


def test_relationship_to_leaf_uses_callsite_context() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "single-pane"},
                children=[
                    node(
                        "call",
                        "deploy",
                        relationship="fn",
                        attributes={"VALUE": "caller"},
                    )
                ],
            ),
            node(
                "functions",
                "functions",
                children=[
                    node(
                        "fn",
                        "echo {{window-name}} {{node-name}} {{VALUE}}",
                        attributes={"VALUE": "target-default"},
                    )
                ],
            ),
        ],
    )

    assert commands(compile_map(raw)) == [
        "deploy",
        "echo ops deploy caller",
    ]


def test_relationship_to_composite_subtree() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "single-pane"},
                children=[node("call", "run", relationship="fn")],
            ),
            node(
                "fn",
                "function label",
                children=[
                    node("one", "echo one {{node-name}}"),
                    node(
                        "two",
                        "echo two {{node-name}}",
                        children=[node("three", "echo three {{node-name}}")],
                    ),
                ],
            ),
        ],
    )

    assert commands(compile_map(raw)) == [
        "run",
        "echo one run",
        "echo two run",
        "echo three run",
    ]


def test_window_relationship_creates_implicit_pane() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("window", "ops", tags=["WINDOW"], relationship="fn"),
            node("fn", "echo {{window-name}}/{{node-name}}"),
        ],
    )

    session = compile_map(raw)
    window = session.windows[0]
    assert window.mode == "single_implicit_pane"
    assert len(window.panes) == 1
    assert commands(session) == ["echo ops/ops"]


def test_detail_precedes_text_and_relationship_then_children_continue() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "single-pane"},
                children=[
                    node(
                        "call",
                        "ignored text",
                        detail="echo detail",
                        relationship="fn",
                        children=[node("tail", "echo tail")],
                    )
                ],
            ),
            node("fn", "echo function"),
        ],
    )

    assert commands(compile_map(raw)) == ["echo detail", "echo function", "echo tail"]


def test_relationship_from_pane_root() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "pane-list"},
                children=[
                    node(
                        "pane",
                        "admin",
                        relationship="fn",
                        attributes={"value": "caller"},
                    )
                ],
            ),
            node(
                "fn",
                "echo {{pane-name}} {{node-name}} {{value}}",
                attributes={"value": "target"},
            ),
        ],
    )

    session = compile_map(raw)
    pane = session.windows[0].panes[0]
    assert pane.title == "admin"
    assert [step.command for step in pane.steps] == ["echo admin admin caller"]


def test_relationship_target_pre_and_env_resolve_with_callsite_override() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "single-pane"},
                children=[
                    node(
                        "call",
                        "deploy",
                        relationship="fn",
                        attributes={"HOST": "callsite.example"},
                    )
                ],
            ),
            node(
                "fn",
                "echo $HOST",
                attributes={
                    "HOST": "target.example",
                    "pre": "echo preparing {{HOST}} for {{node-name}}",
                },
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.steps[0].command == "deploy"
    step = pane.steps[1]
    assert step.command == "echo $HOST"
    assert step.effective_scope.env["HOST"] == "callsite.example"
    assert step.effective_scope.pre == ("echo preparing callsite.example for deploy",)


def test_window_relationship_without_children_overrides_stale_pane_list_mode() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                relationship="fn",
                attributes={"window-mode": "pane-list"},
            ),
            node("fn", "echo okay"),
        ],
    )

    window = compile_map(raw).windows[0]
    assert window.mode == "single_implicit_pane"
    assert [step.command for step in window.panes[0].steps] == ["echo okay"]


def test_multiple_relationships_follow_own_command_then_children() -> None:
    example_path = Path(__file__).parents[1] / "examples" / "multi-relationship-map.json"
    raw = json.loads(example_path.read_text(encoding="utf-8"))

    window = compile_map(raw).windows[0]
    assert window.mode == "pane_list"
    assert [pane.title for pane in window.panes] == ["second pane", "remote host"]
    assert [[step.command for step in pane.steps] for pane in window.panes] == [
        ["uptime", "hostname", "echo test"],
        ["ssh hw0076", "uptime", "hostname", "pwd", "uptime"],
    ]


def test_each_relationship_uses_its_own_defaults_with_callsite_override() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "single-pane"},
                children=[
                    node(
                        "call",
                        "echo own {{VALUE}}",
                        relationships=["one", "two"],
                        attributes={"VALUE": "caller"},
                    )
                ],
            ),
            node("one", "echo one {{VALUE}}", attributes={"VALUE": "first"}),
            node("two", "echo two {{VALUE}}", attributes={"VALUE": "second"}),
        ],
    )

    assert commands(compile_map(raw)) == [
        "echo own caller",
        "echo one caller",
        "echo two caller",
    ]
