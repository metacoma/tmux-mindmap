from __future__ import annotations

import pytest
import yaml
from conftest import compile_map, node

from freeplane_tmux.emitter import session_to_tmuxp
from freeplane_tmux.errors import SemanticError


def test_root_exec_workdir_becomes_tmuxp_session_start_directory() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"exec.workdir": "/srv/project"},
        children=[
            node("window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")])
        ],
    )

    session = compile_map(raw)
    assert session.start_directory == "/srv/project"
    assert session_to_tmuxp(session)["start_directory"] == "/srv/project"


def test_root_exec_workdir_supports_jinja_and_session_name() -> None:
    raw = node(
        "root",
        "demo-{{ vars.target }}",
        attributes={"exec.workdir": "{{ vars.base }}/{{ session.name }}"},
        children=[
            node(
                "vars",
                "vars",
                children=[
                    node("base", "base", detail="/srv"),
                    node("target", "target", detail="prod"),
                ],
            ),
            node(
                "window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")]
            ),
        ],
    )

    assert compile_map(raw).start_directory == "/srv/demo-prod"


def test_blank_root_exec_workdir_is_not_emitted() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"exec.workdir": "   "},
        children=[
            node("window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")])
        ],
    )

    session = compile_map(raw)
    assert session.start_directory is None
    assert "start_directory" not in session_to_tmuxp(session)


def test_exec_workdir_on_non_root_node_does_not_change_session_directory() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"var.workdir": "/expected"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"exec.workdir": "/wrong"},
                children=[node("cmd", "show", detail="echo {{ workdir }}")],
            )
        ],
    )

    session = compile_map(raw)
    assert session.start_directory is None
    assert [step.command for step in session.windows[0].panes[0].steps] == ["echo /expected"]


def test_unresolved_root_exec_workdir_is_rejected() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"exec.workdir": "{{ missing }}"},
        children=[
            node("window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")])
        ],
    )

    with pytest.raises(SemanticError, match=r'undefined template variable "missing"'):
        compile_map(raw)


def test_tmuxp_loader_accepts_emitted_session_start_directory() -> None:
    from tmuxp.workspace.loader import expand

    raw = node(
        "root",
        "demo",
        attributes={"exec.workdir": "/tmp"},
        children=[
            node("window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")])
        ],
    )

    tmuxp = session_to_tmuxp(compile_map(raw))
    expanded = expand(yaml.safe_load(yaml.safe_dump(tmuxp, allow_unicode=True, sort_keys=False)))

    assert expanded["start_directory"] == "/tmp"
