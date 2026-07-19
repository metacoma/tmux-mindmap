from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

FIXTURE_DIR = Path(__file__).parents[1] / "examples" / "history"
pytestmark = pytest.mark.tmuxp_integration


def _cases() -> list[dict[str, str]]:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    return manifest["cases"]


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable:
        return executable
    if os.environ.get("REQUIRE_TMUXP_INTEGRATION") == "1":
        pytest.fail(f"required integration executable is missing: {name}")
    pytest.skip(f"{name} is not installed")


def _runtime_safe_config(expected: dict[str, Any], session_name: str) -> dict[str, Any]:
    config = copy.deepcopy(expected)
    config["session_name"] = session_name

    for window in config["windows"]:
        options = window.setdefault("options", {})
        options["automatic-rename"] = "off"
        for pane in window["panes"]:
            # Command correctness is asserted against the canonical YAML in
            # test_history_fixtures.py. Keep only the generated OSC title command,
            # then replace executable payloads with one harmless hold process.
            title_commands = [
                command
                for command in pane["shell_command"]
                if command.startswith("printf '\\033]2;%s\\033\\\\' ")
            ]
            pane["shell_command"] = [*title_commands[:1], "exec sleep 60"]
            pane.pop("environment", None)

    return config


def _expected_pane_title(pane: dict[str, Any]) -> str | None:
    for command in pane["shell_command"]:
        if command.startswith("printf '\\033]2;%s\\033\\\\' "):
            return shlex.split(command)[-1]
    return None


def _run(command: list[str], *, env: dict[str, str], check: bool = True) -> str:
    completed = subprocess.run(
        command,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {command!r}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed.stdout


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_tmuxp_load_creates_expected_window_and_pane_structure(
    case: dict[str, str],
    tmp_path: Path,
) -> None:
    tmuxp = _required_executable("tmuxp")
    tmux = _required_executable("tmux")

    expected = yaml.safe_load((FIXTURE_DIR / case["tmuxp"]).read_text(encoding="utf-8"))
    session_name = f"fp_tmux_it_{case['name'].replace('-', '_')}_{os.getpid()}"
    runtime_config = _runtime_safe_config(expected, session_name)
    runtime_path = tmp_path / f"{case['name']}.tmuxp.yaml"
    runtime_path.write_text(
        yaml.safe_dump(runtime_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    socket_dir = tmp_path / "tmux-socket"
    socket_dir.mkdir(mode=0o700)
    env = os.environ.copy()
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env["TMUX_TMPDIR"] = str(socket_dir)

    try:
        _run([tmuxp, "load", "-d", str(runtime_path)], env=env)
        output = _run(
            [
                tmux,
                "list-windows",
                "-t",
                session_name,
                "-F",
                "#{window_index}\t#{window_name}\t#{window_panes}",
            ],
            env=env,
        )

        actual_windows = [
            (window_index, name, int(pane_count))
            for window_index, name, pane_count in (line.split("\t") for line in output.splitlines())
        ]
        expected_structure = [
            (window["window_name"], len(window["panes"])) for window in expected["windows"]
        ]

        assert [(name, pane_count) for _, name, pane_count in actual_windows] == (
            expected_structure
        )

        pane_output = _run(
            [
                tmux,
                "list-panes",
                "-s",
                "-t",
                session_name,
                "-F",
                "#{window_index}\t#{pane_index}\t#{pane_title}",
            ],
            env=env,
        )
        actual_titles: dict[str, list[tuple[int, str]]] = {}
        for window_index, pane_index, pane_title in (
            line.split("\t", 2) for line in pane_output.splitlines()
        ):
            actual_titles.setdefault(window_index, []).append((int(pane_index), pane_title))

        for (window_index, _, _), expected_window in zip(
            actual_windows,
            expected["windows"],
            strict=True,
        ):
            ordered_actual = [
                title for _, title in sorted(actual_titles[window_index], key=lambda item: item[0])
            ]
            expected_titles = [_expected_pane_title(pane) for pane in expected_window["panes"]]
            for actual_title, expected_title in zip(
                ordered_actual,
                expected_titles,
                strict=True,
            ):
                if expected_title is not None:
                    assert actual_title == expected_title
    finally:
        _run([tmux, "kill-server"], env=env, check=False)
