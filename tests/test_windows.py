from __future__ import annotations

from conftest import compile_map, node


def test_plain_window_children_become_one_implicit_pane() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("one", "echo one"), node("two", "echo two")],
            )
        ],
    )

    window = compile_map(raw).windows[0]
    assert window.mode == "single_implicit_pane"
    assert len(window.panes) == 1
    assert [step.command for step in window.panes[0].steps] == ["echo one", "echo two"]


def test_complex_children_become_pane_list() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[
                    node("pane-a", "editor", children=[node("cmd-a", "nvim")]),
                    node("pane-b", "logs", detail="journalctl -f"),
                ],
            )
        ],
    )

    window = compile_map(raw).windows[0]
    assert window.mode == "pane_list"
    assert [pane.title for pane in window.panes] == ["editor", "logs"]
    assert [[step.command for step in pane.steps] for pane in window.panes] == [
        ["nvim"],
        ["journalctl -f"],
    ]


def test_intermediate_path_attributes_are_inherited() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"ROOT": "root"},
        children=[
            node(
                "group",
                "group",
                attributes={"VALUE": "from-group"},
                children=[
                    node(
                        "window",
                        "ops",
                        tags=["WINDOW"],
                        children=[node("command", "echo {{ROOT}} {{VALUE}}")],
                    )
                ],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["echo root from-group"]


def test_mixed_window_groups_consecutive_commands_and_keeps_named_panes() -> None:
    raw = node(
        "root",
        "playground",
        children=[
            node(
                "window",
                "hello-win",
                tags=["WINDOW"],
                children=[
                    node("pwd", "echo pwd"),
                    node("uptime", "echo uptime"),
                    node(
                        "second-pane",
                        "second pane",
                        children=[node("who", "who")],
                    ),
                ],
            )
        ],
    )

    window = compile_map(raw).windows[0]

    assert window.mode == "mixed"
    assert len(window.panes) == 2
    assert [pane.title for pane in window.panes] == [None, "second pane"]
    assert [[step.command for step in pane.steps] for pane in window.panes] == [
        ["echo pwd", "echo uptime"],
        ["who"],
    ]


def test_window_sequence_preserves_command_runs_around_named_panes() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[
                    node("before", "echo before"),
                    node(
                        "remote",
                        "remote",
                        children=[node("ssh", "ssh host")],
                    ),
                    node("after", "echo after"),
                ],
            )
        ],
    )

    window = compile_map(raw).windows[0]

    assert window.mode == "mixed"
    assert [pane.title for pane in window.panes] == [None, "remote", None]
    assert [[step.command for step in pane.steps] for pane in window.panes] == [
        ["echo before"],
        ["ssh host"],
        ["echo after"],
    ]


def test_command_and_pane_tags_resolve_ambiguous_window_children() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[
                    node(
                        "command-tree",
                        "echo command-root",
                        tags=["COMMAND"],
                        children=[node("child", "echo child")],
                    ),
                    node("empty-pane", "shell", tags=["PANE"]),
                ],
            )
        ],
    )

    window = compile_map(raw).windows[0]

    assert window.mode == "mixed"
    assert [pane.title for pane in window.panes] == [None, "shell"]
    assert [step.command for step in window.panes[0].steps] == [
        "echo command-root",
        "echo child",
    ]
    assert window.panes[1].steps == ()


def test_conflicting_window_child_role_tags_are_rejected() -> None:
    import pytest

    from freeplane_tmux.errors import SemanticError

    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("ambiguous", "echo nope", tags=["PANE", "COMMAND"])],
            )
        ],
    )

    with pytest.raises(SemanticError, match="both PANE and COMMAND"):
        compile_map(raw)
