from __future__ import annotations

import pytest
from conftest import compile_map, node

from freeplane_tmux.errors import SemanticError
from freeplane_tmux.shell import pane_shell_commands


def test_pre_is_accumulated_and_emitted_once_per_new_scope() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"pre": "echo root"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"pre": "echo window"},
                children=[
                    node(
                        "pane",
                        "admin",
                        attributes={"pre": "echo pane"},
                        children=[
                            node("first", "echo first"),
                            node("second", "echo second"),
                        ],
                    )
                ],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    emitted = pane_shell_commands(pane)
    assert emitted.count("echo root") == 1
    assert emitted.count("echo window") == 1
    assert emitted.count("echo pane") == 1
    assert emitted.index("echo pane") < emitted.index("echo first")
    assert emitted.index("echo first") < emitted.index("echo second")


def test_env_and_alias_bootstrap_are_injected_into_ssh_and_sudo() -> None:
    alias = node("alias", "ll", tags=["ALIAS"], detail="ls -la {{DIR}}")
    raw = node(
        "root",
        "demo",
        attributes={"TOKEN": "secret", "DIR": "/srv"},
        children=[
            alias,
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("ssh", "ssh host"), node("sudo", "sudo bash")],
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    emitted = pane_shell_commands(pane)
    ssh_command = next(command for command in emitted if command.startswith("ssh "))
    sudo_command = next(command for command in emitted if command.startswith("sudo "))

    for rewritten in (ssh_command, sudo_command):
        assert "export TOKEN=secret" in rewritten
        assert "alias ll=" in rewritten
        assert "ls -la /srv" in rewritten
        assert "--rcfile" in rewritten


def test_alias_uses_late_resolve_from_descendant_scope() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("alias", "go", tags=["ALIAS"], detail="cd {{WORKDIR}}"),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "single-pane"},
                children=[node("command", "go", attributes={"WORKDIR": "/tmp/project"})],
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.base_scope.aliases == {}
    assert pane.steps[0].effective_scope.aliases == {"go": "cd /tmp/project"}
    emitted = pane_shell_commands(pane)
    assert "alias go='cd /tmp/project'" in emitted
    assert emitted.index("alias go='cd /tmp/project'") < emitted.index("go")


def test_unresolved_alias_fails_at_executable_callsite() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("alias", "go", tags=["ALIAS"], detail="cd {{MISSING}}"),
            node("window", "ops", tags=["WINDOW"], children=[node("command", "go")]),
        ],
    )

    with pytest.raises(SemanticError, match="alias 'go'.*MISSING"):
        compile_map(raw)


def test_alias_detail_wins_over_relationship() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "alias",
                "run",
                tags=["ALIAS"],
                detail="echo detail",
                relationship="fn",
            ),
            node("window", "ops", tags=["WINDOW"], children=[node("cmd", "run")]),
            node("fn", "echo relationship"),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.steps[0].effective_scope.aliases == {"run": "echo detail"}


def test_non_shell_sudo_command_is_not_rewritten() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"TOKEN": "secret"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("command", "sudo apt-get update")],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane_shell_commands(pane)[-1] == "sudo apt-get update"
