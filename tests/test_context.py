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


def test_pane_title_uses_osc_without_allow_set_title() -> None:
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
                        "pane",
                        "remote host",
                        children=[node("command", "uptime")],
                    )
                ],
            )
        ],
    )

    emitted = pane_shell_commands(compile_map(raw).windows[0].panes[0])
    assert emitted[0] == "printf '\\033]2;%s\\033\\\\' 'remote host'"
    assert all("allow-set-title" not in command for command in emitted)


def test_pane_name_builtin_is_available_across_pane_execution_path() -> None:
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
                        "remote host",
                        children=[
                            node(
                                "command",
                                "echo own {{ pane-name }}",
                                relationships=["helper"],
                                children=[node("tail", "echo child {{ pane-name }}")],
                            )
                        ],
                    )
                ],
            ),
            node("helper", "echo relationship {{ pane-name }}"),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.base_scope.vars["pane-name"] == "remote host"
    assert [step.command for step in pane.steps] == [
        "echo own remote host",
        "echo relationship remote host",
        "echo child remote host",
    ]
    assert all(step.effective_scope.vars["pane-name"] == "remote host" for step in pane.steps)


def test_pane_name_builtin_is_empty_for_unnamed_implicit_pane() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"window-mode": "single-pane"},
                children=[node("command", "printf '<%s>\\n' '{{ pane-name }}'")],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.title is None
    assert pane.base_scope.vars["pane-name"] == ""
    assert [step.command for step in pane.steps] == ["printf '<%s>\\n' ''"]


def test_jinja_expands_session_window_pane_and_node_names() -> None:
    raw = node(
        "root",
        "session-{{ suffix }}",
        attributes={"suffix": "lab", "host": "mcmp2"},
        children=[
            node(
                "window",
                "{{ host }}",
                tags=["WINDOW"],
                attributes={"window-mode": "pane-list"},
                children=[
                    node(
                        "pane",
                        "{{ window-name }}",
                        children=[
                            node("ssh", "ssh {{ pane-name }}"),
                            node(
                                "health",
                                "health {{ window-name }}",
                                detail="echo {{ node-name }}",
                            ),
                        ],
                    )
                ],
            )
        ],
    )

    session = compile_map(raw)
    window = session.windows[0]
    pane = window.panes[0]

    assert session.session_name == "session-lab"
    assert window.name == "mcmp2"
    assert pane.title == "mcmp2"
    assert pane.base_scope.vars["pane-name"] == "mcmp2"
    assert pane_shell_commands(pane)[0] == "printf '\\033]2;%s\\033\\\\' mcmp2"
    assert [step.display_name for step in pane.steps] == ["ssh mcmp2", "health mcmp2"]
    assert [step.command for step in pane.steps] == ["ssh mcmp2", "echo health mcmp2"]


def test_unresolved_template_in_pane_name_is_rejected() -> None:
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
                        "{{ missing-pane-name }}",
                        children=[node("command", "uptime")],
                    )
                ],
            )
        ],
    )

    with pytest.raises(SemanticError, match="pane name.*missing-pane-name"):
        compile_map(raw)
