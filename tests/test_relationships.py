from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import compile_map, node

from freeplane_tmux.errors import SemanticError

FIXTURE_DIR = Path(__file__).parents[1] / "examples" / "history"


def test_relationship_args_use_defaults_and_callsite_overrides() -> None:
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
                                        attributes={"username": "alice", "password": "secret"},
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
                attributes={"tmux.mode": "single-pane"},
                children=[
                    node(
                        "connect",
                        "connect",
                        relationship="mongo-helper",
                        attributes={
                            "arg.username": "{{ vars.credentials.prod.mysql.username }}",
                            "arg.password": "{{ vars.credentials.prod.mysql.password }}",
                            "arg.db": "jira_cmdb_sam",
                        },
                    )
                ],
            ),
            node(
                "mongo-helper",
                "mongo helper",
                attributes={"default.auth_source": "admin"},
                detail=(
                    "mongosh 'mongodb://{{ args.username }}:{{ args.password }}@host/"
                    "?authSource={{ args.auth_source }}&db={{ args.db }}'"
                ),
            ),
        ],
    )

    pane = compile_map(raw).windows[0].panes[0]
    assert [step.command for step in pane.steps] == [
        "mongosh 'mongodb://alice:secret@host/?authSource=admin&db=jira_cmdb_sam'"
    ]


def test_relationship_does_not_implicitly_merge_callsite_attributes_into_args() -> None:
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
                        attributes={"username": "alice"},
                    )
                ],
            ),
            node("mongo-helper", "mongo helper", detail="echo {{ args.username }}"),
        ],
    )

    with pytest.raises(SemanticError, match=r'undefined template variable "args.username"'):
        compile_map(raw)


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
