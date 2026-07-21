from __future__ import annotations

import re

import pytest
from conftest import compile_map, node

from freeplane_tmux.errors import SemanticError
from freeplane_tmux.shell import pane_shell_commands


def test_exec_pre_is_accumulated_in_order() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"exec.pre": "echo root", "var.region": "eu"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"exec.pre": "echo window {{ region }}"},
                children=[
                    node(
                        "pane",
                        "admin",
                        attributes={"exec.pre": "echo pane {{ pane.name }}"},
                        children=[node("first", "run", detail="echo first")],
                    )
                ],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.base_scope.pre == ("echo root", "echo window eu", "echo pane admin")
    emitted = pane_shell_commands(pane)
    assert emitted[:4] == [
        "printf '\\033]2;%s\\033\\\\' admin",
        "echo root",
        "echo window eu",
        "echo pane admin",
    ]


def test_alias_uses_late_resolve_from_descendant_scoped_variable() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("alias", "go", tags=["ALIAS"], detail="cd {{ workdir }}"),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node("command", "run", detail="go", attributes={"var.workdir": "/tmp/project"})
                ],
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.base_scope.aliases == {}
    assert pane.steps[0].effective_scope.aliases == {"go": "cd /tmp/project"}
    emitted = pane_shell_commands(pane)
    assert emitted[0:3] == ["shopt -s expand_aliases", "alias go='cd /tmp/project'", "clear"]


def test_env_and_alias_bootstrap_are_injected_into_ssh_and_sudo() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"env.TOKEN": "secret"},
        children=[
            node("alias", "ll", tags=["ALIAS"], detail="ls -la {{ env.TOKEN }}"),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node("ssh", "connect", detail="ssh host"),
                    node("sudo", "root shell", detail="sudo bash"),
                ],
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    emitted = pane_shell_commands(pane)
    ssh_command = next(command for command in emitted if command.startswith("ssh "))
    sudo_command = next(
        command
        for command in emitted
        if "bash --noprofile --rcfile" in command and not command.startswith("ssh ")
    )

    for rewritten in (ssh_command, sudo_command):
        assert "export TOKEN=secret" in rewritten
        assert "alias ll=" in rewritten
        assert "ls -la secret" in rewritten
        assert "clear" in rewritten
        assert "--rcfile" in rewritten


def test_vars_attributes_and_nested_nodes_render() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "vars",
                "vars",
                children=[
                    node(
                        "credentials",
                        "credentials",
                        children=[
                            node(
                                "prod",
                                "prod",
                                children=[
                                    node(
                                        "mysql",
                                        "mysql",
                                        attributes={"username": "aaa", "password": "xxx"},
                                        children=[
                                            node(
                                                "env1",
                                                "env1",
                                                attributes={"env_name": "env1", "env_status": "OK"},
                                            )
                                        ],
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[
                    node(
                        "cmd",
                        "show",
                        detail=(
                            "echo {{ vars.credentials.prod.mysql.username }} "
                            "{{ vars.credentials.prod.mysql.password }} "
                            "{{ vars.credentials.prod.mysql.env1.env_name }} "
                            "{{ vars.credentials.prod.mysql.env1.env_status }}"
                        ),
                    )
                ],
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["echo aaa xxx env1 OK"]


def test_leaf_child_detail_becomes_scalar_value() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "vars",
                "vars",
                children=[
                    node(
                        "credentials",
                        "credentials",
                        children=[
                            node(
                                "prod",
                                "prod",
                                children=[
                                    node(
                                        "mysql",
                                        "mysql",
                                        children=[node("username", "username", detail="aaa")],
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[
                    node("cmd", "show", detail="echo {{ vars.credentials.prod.mysql.username }}")
                ],
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["echo aaa"]


def test_duplicate_attribute_and_child_under_vars_is_rejected() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "vars",
                "vars",
                children=[
                    node(
                        "db",
                        "db",
                        attributes={"username": "aaa"},
                        children=[node("username", "username", detail="bbb")],
                    )
                ],
            ),
            node(
                "window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")]
            ),
        ],
    )

    with pytest.raises(SemanticError, match=r"Duplicate variable path: vars\.db\.username"):
        compile_map(raw)


def test_object_as_scalar_is_rejected_with_available_fields() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node("vars", "vars", children=[node("db", "db", attributes={"username": "aaa"})]),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("cmd", "show", detail="echo {{ vars.db }}")],
            ),
        ],
    )

    with pytest.raises(
        SemanticError, match=r"Cannot render object vars\.db as a scalar value.*username"
    ):
        compile_map(raw)


def test_missing_leaf_detail_is_rejected() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "vars", "vars", children=[node("db", "db", children=[node("username", "username")])]
            ),
            node(
                "window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")]
            ),
        ],
    )

    with pytest.raises(SemanticError, match=r"Variable vars\.db\.username has no value"):
        compile_map(raw)


def test_explicit_list_is_shell_quoted_and_scalar_with_spaces_is_not_a_list() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "vars",
                "vars",
                children=[
                    node(
                        "ips",
                        "ips",
                        tags=["LIST"],
                        children=[
                            node("ip1", "10.10.0.1"),
                            node("ip2", "two words"),
                            node("ip3", "$(unsafe)"),
                        ],
                    ),
                    node("public_ips", "public_ips", detail="10.10.0.1 10.10.0.2"),
                ],
            ),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node(
                        "cmd1",
                        "show list",
                        detail='for i in {{ vars.ips }}; do printf "%s\\n" "$i"; done',
                    ),
                    node("cmd2", "show scalar", detail="echo {{ vars.public_ips }}"),
                ],
            ),
        ],
    )

    commands = [step.command for step in compile_map(raw).windows[0].panes[0].steps]
    assert (
        commands[0]
        == "for i in 10.10.0.1 'two words' '$(unsafe)'; do printf \"%s\\n\" \"$i\"; done"
    )
    assert commands[1] == "echo 10.10.0.1 10.10.0.2"


def test_scoped_variables_and_runtime_attributes_have_separate_namespaces() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"var.region": "eu"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"host": "server01"},
                children=[
                    node(
                        "pane",
                        "db",
                        attributes={"database": "jira_cmdb"},
                        children=[
                            node(
                                "connect",
                                "connect",
                                attributes={"db": "jira_cmdb_sam"},
                                detail=(
                                    "echo {{ region }} {{ window.host }} "
                                    "{{ pane.database }} {{ node.db }}"
                                ),
                            )
                        ],
                    )
                ],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["echo eu server01 jira_cmdb jira_cmdb_sam"]


def test_ordinary_attribute_is_available_as_local_flat_variable() -> None:
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
                        "cmd",
                        "show",
                        detail="echo {{ user }} {{ node.user }}",
                        attributes={"user": "bebebeka"},
                    )
                ],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == ["echo bebebeka bebebeka"]


@pytest.mark.parametrize(
    "template",
    [
        "{{ root.db.user }}",
        "{{ window-name }}",
        "{{ pane-name }}",
        "{{ node-name }}",
        "{{ session-name }}",
        "{{ scoped.region }}",
    ],
)
def test_removed_legacy_placeholders_are_undefined(template: str) -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[node("cmd", "show", detail=f"echo {template}")],
            )
        ],
    )

    with pytest.raises(SemanticError):
        compile_map(raw)


@pytest.mark.parametrize(
    "template",
    [
        "{{ script1 }}",
        "{{ node.script1 }}",
        "{{ window.script1 }}",
        "{{ pane.script1 }}",
        "{{ vars.script1 }}",
    ],
)
def test_script1_stays_service_only(template: str) -> None:
    raw = node(
        "root",
        "demo",
        attributes={"script1": "println('hi')"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[
                    node("pane", "pane", children=[node("cmd", "show", detail=f"echo {template}")])
                ],
            )
        ],
    )

    with pytest.raises(SemanticError):
        compile_map(raw)


@pytest.mark.parametrize("template", ["{{ exec.pre }}", "{{ tmux.mode }}"])
def test_service_attributes_do_not_become_plain_template_variables(template: str) -> None:
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
                        "cmd",
                        "show",
                        detail=f"echo {template}",
                        attributes={"exec.pre": "echo hidden"},
                    )
                ],
            )
        ],
    )

    with pytest.raises(SemanticError):
        compile_map(raw)


def test_env_requires_explicit_prefix_and_inherits() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"env.PROJECT_DIR": "/srv/root", "TOKEN": "not-env"},
        children=[
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                attributes={"env.PROJECT_DIR": "/srv/window"},
                children=[
                    node(
                        "pane",
                        "main",
                        attributes={"env.TOKEN": "secret"},
                        children=[
                            node(
                                "cmd",
                                "show",
                                detail="echo {{ env.PROJECT_DIR }} {{ env.TOKEN }}",
                            )
                        ],
                    )
                ],
            )
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert pane.base_scope.env == {"PROJECT_DIR": "/srv/window", "TOKEN": "secret"}
    assert [step.command for step in pane.steps] == ["echo /srv/window secret"]
    with pytest.raises(SemanticError, match=r'undefined template variable "env.TOKEN"'):
        compile_map(
            node(
                "root",
                "demo",
                attributes={"TOKEN": "not-env"},
                children=[
                    node(
                        "window",
                        "ops",
                        tags=["WINDOW"],
                        children=[node("cmd", "show", detail="echo {{ env.TOKEN }}")],
                    )
                ],
            )
        )


def test_reserved_scoped_variable_names_are_rejected() -> None:
    raw = node(
        "root",
        "demo",
        attributes={"var.window": "oops"},
        children=[
            node("window", "ops", tags=["WINDOW"], children=[node("cmd", "show", detail="echo ok")])
        ],
    )

    with pytest.raises(SemanticError, match='Scoped variable name "window" is reserved'):
        compile_map(raw)


@pytest.mark.parametrize("attribute_name", ["node", "window", "pane", "session", "env"])
def test_reserved_plain_attribute_names_are_rejected(attribute_name: str) -> None:
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
                        "cmd",
                        "show",
                        detail="echo ok",
                        attributes={attribute_name: "bad"},
                    )
                ],
            )
        ],
    )

    with pytest.raises(
        SemanticError,
        match=rf'ordinary attribute name "{attribute_name}" is reserved in node cmd "show"',
    ):
        compile_map(raw)


def test_strict_undefined_reports_available_neighbors() -> None:
    raw = node(
        "root",
        "demo",
        children=[
            node(
                "vars",
                "vars",
                children=[
                    node(
                        "credentials",
                        "credentials",
                        children=[
                            node("prod", "prod", attributes={"username": "aaa", "password": "xxx"})
                        ],
                    )
                ],
            ),
            node(
                "window",
                "ops",
                tags=["WINDOW"],
                children=[
                    node(
                        "cmd",
                        "show",
                        detail="echo {{ vars.credentials.prod.usrename }}",
                    )
                ],
            ),
        ],
    )

    with pytest.raises(
        SemanticError,
        match=re.escape('undefined template variable "vars.credentials.prod.usrename"'),
    ) as excinfo:
        compile_map(raw)
    assert "username, password" in str(excinfo.value)
