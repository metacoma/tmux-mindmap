from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import compile_map, node

from freeplane_tmux.errors import SemanticError

FIXTURE_DIR = Path(__file__).parents[1] / "examples" / "history"


def commands(session) -> list[str]:
    return [step.command for step in session.windows[0].panes[0].steps]


def pane_commands(window) -> list[list[str]]:
    return [[step.command for step in pane.steps] for pane in window.panes]


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
                        "echo {{window.name}} {{node-name}} {{VALUE}}",
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
                "echo {{pane.name}} {{node-name}} {{value}}",
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


def test_multiple_relationships_follow_own_command_then_children() -> None:
    example_path = Path(__file__).parents[1] / "examples" / "multi-relationship-map.json"
    raw = json.loads(example_path.read_text(encoding="utf-8"))

    window = compile_map(raw).windows[0]
    assert window.mode == "pane_list"
    assert [pane.title for pane in window.panes] == ["second pane", "remote host"]
    assert pane_commands(window) == [
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


def test_window_relationship_rerenders_inherited_panes_in_derived_context() -> None:
    raw = json.loads((FIXTURE_DIR / "window-inheritance.map.json").read_text(encoding="utf-8"))

    session = compile_map(raw)
    mcmp3 = session.windows[2]

    assert mcmp3.name == "mcmp3"
    assert [pane.title for pane in mcmp3.panes] == ["pane1", "pane2", None, "additional pane"]
    assert mcmp3.panes[0].base_scope.pre == ("ssh hw0076",)
    assert [step.command for step in mcmp3.panes[0].steps] == [
        "ping mcmp3.mgmt.mansion.shitcluster.io"
    ]
    assert [step.command for step in mcmp3.panes[1].steps] == ["ssh mcmp3"]
    assert [step.command for step in mcmp3.panes[2].steps] == ["additional command"]
    assert [step.command for step in mcmp3.panes[3].steps] == ['echo "additional pane"']


def test_window_relationship_merges_window_attributes() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"domain": "example.org"},
        children=[
            node(
                "base",
                "base",
                tags=["WINDOW"],
                attributes={
                    "host": "old",
                    "role": "db",
                    "target": "{{ window.host }}.{{ domain }}",
                },
                children=[
                    node(
                        "pane",
                        "pane",
                        children=[
                            node(
                                "command",
                                (
                                    "echo {{ window.name }} {{ window.host }} "
                                    "{{ window.role }} {{ window.target }}"
                                ),
                            )
                        ],
                    )
                ],
            ),
            node(
                "derived",
                "derived",
                tags=["WINDOW"],
                attributes={"host": "new"},
                relationship="base",
            ),
        ],
    )

    derived = compile_map(raw).windows[1]
    assert pane_commands(derived) == [["echo derived new db new.example.org"]]


def test_multiple_window_relationships_follow_precedence_order() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "base1",
                "{{ window.host }}",
                tags=["WINDOW"],
                attributes={"host": "one", "color": "blue"},
                children=[
                    node(
                        "base1-shared",
                        "shared",
                        children=[node("base1-shared-cmd", "echo base1 {{ window.color }}")],
                    ),
                    node(
                        "base1-one-only",
                        "one-only",
                        children=[node("base1-one-only-cmd", "echo one-only")],
                    ),
                ],
            ),
            node(
                "base2",
                "{{ window.host }}",
                tags=["WINDOW"],
                attributes={"host": "two", "color": "green"},
                children=[
                    node(
                        "base2-shared",
                        "shared",
                        children=[node("base2-shared-cmd", "echo base2 {{ window.color }}")],
                    ),
                    node(
                        "base2-two-only",
                        "two-only",
                        children=[node("base2-two-only-cmd", "echo two-only")],
                    ),
                ],
            ),
            node(
                "derived",
                "{{ window.host }}",
                tags=["WINDOW"],
                attributes={"host": "three", "color": "red"},
                relationships=["base1", "base2"],
                children=[
                    node(
                        "derived-shared",
                        "shared",
                        children=[node("derived-shared-cmd", "echo derived {{ window.color }}")],
                    ),
                    node(
                        "derived-local", "local", children=[node("derived-local-cmd", "echo local")]
                    ),
                ],
            ),
        ],
    )

    derived = compile_map(raw).windows[2]
    assert derived.name == "three"
    assert [pane.title for pane in derived.panes] == ["one-only", "two-only", "shared", "local"]
    assert pane_commands(derived) == [
        ["echo one-only"],
        ["echo two-only"],
        ["echo derived red"],
        ["echo local"],
    ]


def test_local_pane_replaces_inherited_pane_with_same_rendered_name() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "base",
                "base",
                tags=["WINDOW"],
                children=[
                    node(
                        "base-pane",
                        "{{ window.name }}",
                        children=[node("base-pane-cmd", "echo base {{ window.name }}")],
                    ),
                ],
            ),
            node(
                "derived",
                "derived",
                tags=["WINDOW"],
                relationship="base",
                children=[
                    node(
                        "pane",
                        "{{ window.name }}",
                        children=[node("command", "echo local {{ window.name }}")],
                    ),
                ],
            ),
        ],
    )

    derived = compile_map(raw).windows[1]
    assert [pane.title for pane in derived.panes] == ["derived"]
    assert pane_commands(derived) == [["echo local derived"]]


def test_window_inheritance_keeps_implicit_command_panes_and_order() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "base",
                "base",
                tags=["WINDOW"],
                children=[
                    node("cmd1", "echo base-one"),
                    node("cmd2", "echo base-two"),
                    node("base-named", "named", children=[node("base-named-cmd", "echo named")]),
                ],
            ),
            node(
                "derived",
                "derived",
                tags=["WINDOW"],
                relationship="base",
                children=[
                    node("cmd3", "echo local-one"),
                    node(
                        "derived-other", "other", children=[node("derived-other-cmd", "echo other")]
                    ),
                ],
            ),
        ],
    )

    derived = compile_map(raw).windows[1]
    assert [pane.title for pane in derived.panes] == [None, "named", None, "other"]
    assert pane_commands(derived) == [
        ["echo base-one", "echo base-two"],
        ["echo named"],
        ["echo local-one"],
        ["echo other"],
    ]


def test_window_inheritance_cycle_detection_reports_chain() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("a", "A", tags=["WINDOW"], relationship="b"),
            node("b", "B", tags=["WINDOW"], relationship="a"),
        ],
    )

    with pytest.raises(
        SemanticError, match=r"window inheritance cycle detected: a:A -> b:B -> a:A"
    ):
        compile_map(raw)


def test_window_relationship_target_must_be_window() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("window", "ops", tags=["WINDOW"], relationship="fn"),
            node("fn", "echo nope"),
        ],
    )

    with pytest.raises(SemanticError, match=r"must be a WINDOW node"):
        compile_map(raw)


def test_window_relationship_self_reference_is_rejected() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("window", "ops", tags=["WINDOW"], relationship="window"),
        ],
    )

    with pytest.raises(SemanticError, match=r"cannot inherit from itself"):
        compile_map(raw)
