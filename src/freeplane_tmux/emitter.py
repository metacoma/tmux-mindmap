from __future__ import annotations

from typing import Any

import yaml

from .models import SessionSpec
from .shell import pane_shell_commands


def session_to_tmuxp(session: SessionSpec) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for window in session.windows:
        panes: list[dict[str, Any]] = []
        for pane in window.panes:
            pane_config: dict[str, Any] = {
                "shell_command": pane_shell_commands(pane),
            }
            if pane.base_scope.env:
                pane_config["environment"] = dict(pane.base_scope.env)
            panes.append(pane_config)

        if panes:
            windows.append(
                {
                    "window_name": window.name,
                    "options": {
                        "pane-border-status": "top",
                        "pane-border-format": "#{pane_index}: #{pane_title}",
                    },
                    "panes": panes,
                }
            )

    return {"session_name": session.session_name, "windows": windows}


def dump_tmuxp_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
