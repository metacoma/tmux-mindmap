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
