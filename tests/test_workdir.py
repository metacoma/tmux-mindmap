from __future__ import annotations

import pytest
from conftest import compile_map, node

from freeplane_tmux.emitter import session_to_tmuxp
from freeplane_tmux.errors import SemanticError


def test_root_workdir_becomes_tmuxp_session_start_directory() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"workdir": "/srv/project"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("command", "pwd")],
            )
        ],
    )

    session = compile_map(raw)

    assert session.start_directory == "/srv/project"
    assert session_to_tmuxp(session)["start_directory"] == "/srv/project"


def test_root_workdir_supports_jinja_and_session_name() -> None:
    raw = node(
        "root",
        "{{ project }}",
        attributes={
            "base": "/srv",
            "project": "mindmap",
            "workdir": "{{ base }}/{{ session-name }}",
        },
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("command", "pwd")],
            )
        ],
    )

    session = compile_map(raw)

    assert session.session_name == "mindmap"
    assert session.start_directory == "/srv/mindmap"


def test_blank_root_workdir_is_not_emitted() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"workdir": "   "},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("command", "pwd")],
            )
        ],
    )

    session = compile_map(raw)
    config = session_to_tmuxp(session)

    assert session.start_directory is None
    assert "start_directory" not in config


def test_workdir_on_non_root_node_does_not_change_session_directory() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"workdir": "/wrong"},
                children=[node("command", "echo {{ workdir }}")],
            )
        ],
    )

    session = compile_map(raw)

    assert session.start_directory is None
    assert session.windows[0].panes[0].steps[0].command == "echo /wrong"


def test_unresolved_root_workdir_is_rejected() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"workdir": "{{ missing }}"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("command", "pwd")],
            )
        ],
    )

    with pytest.raises(SemanticError, match="session workdir.*missing"):
        compile_map(raw)


def test_tmuxp_loader_accepts_emitted_session_start_directory() -> None:
    pytest.importorskip("tmuxp")
    from tmuxp.workspace.loader import expand

    raw = node(
        "root",
        "demo",
        attributes={"workdir": "/tmp"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("command", "pwd")],
            )
        ],
    )

    expanded = expand(session_to_tmuxp(compile_map(raw)))

    assert expanded["start_directory"] == "/tmp"
