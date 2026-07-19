from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import time
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
            hold_command = "exec sleep 60"
            if title_commands:
                # Keep the title update and the hold process in one shell command.
                # A prompt rendered between separate commands may emit its own OSC
                # title and overwrite the generated pane name with the hostname.
                hold_command = f"{title_commands[0]}; {hold_command}"
            pane["shell_command"] = [hold_command]
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


TEST_SESSION_COLUMNS = "240"
TEST_SESSION_LINES = "80"
TEST_SESSION_SIZE = f"{TEST_SESSION_COLUMNS}x{TEST_SESSION_LINES}"


def _read_pane_titles(
    tmux: str,
    session_name: str,
    *,
    env: dict[str, str],
) -> dict[str, list[tuple[int, str]]]:
    output = _run(
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
    titles: dict[str, list[tuple[int, str]]] = {}
    for window_index, pane_index, pane_title in (
        line.split("\t", 2) for line in output.splitlines()
    ):
        titles.setdefault(window_index, []).append((int(pane_index), pane_title))
    return titles


def _wait_for_expected_titles(
    tmux: str,
    session_name: str,
    expected_titles: dict[str, list[str | None]],
    *,
    env: dict[str, str],
) -> dict[str, list[tuple[int, str]]]:
    deadline = time.monotonic() + 5
    actual: dict[str, list[tuple[int, str]]] = {}
    while time.monotonic() < deadline:
        actual = _read_pane_titles(tmux, session_name, env=env)
        matches = True
        for window_index, wanted in expected_titles.items():
            ordered = [title for _, title in sorted(actual.get(window_index, []))]
            if len(ordered) != len(wanted):
                matches = False
                break
            if any(
                expected is not None and current != expected
                for current, expected in zip(ordered, wanted, strict=True)
            ):
                matches = False
                break
        if matches:
            return actual
        time.sleep(0.05)
    return actual


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
    runtime_start_directory: Path | None = None
    if "start_directory" in expected:
        runtime_start_directory = tmp_path / "session-workdir"
        runtime_start_directory.mkdir()
        runtime_config["start_directory"] = str(runtime_start_directory)
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
    env["COLUMNS"] = TEST_SESSION_COLUMNS
    env["LINES"] = TEST_SESSION_LINES

    try:
        _run([tmux, "start-server"], env=env)
        _run([tmux, "set-option", "-g", "default-size", TEST_SESSION_SIZE], env=env)
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

        expected_titles_by_window = {
            window_index: [_expected_pane_title(pane) for pane in expected_window["panes"]]
            for (window_index, _, _), expected_window in zip(
                actual_windows,
                expected["windows"],
                strict=True,
            )
        }
        actual_titles = _wait_for_expected_titles(
            tmux,
            session_name,
            expected_titles_by_window,
            env=env,
        )

        for window_index, expected_titles in expected_titles_by_window.items():
            ordered_actual = [
                title for _, title in sorted(actual_titles[window_index], key=lambda item: item[0])
            ]
            for actual_title, expected_title in zip(
                ordered_actual,
                expected_titles,
                strict=True,
            ):
                if expected_title is not None:
                    assert actual_title == expected_title

        if runtime_start_directory is not None:
            pane_paths = _run(
                [
                    tmux,
                    "list-panes",
                    "-s",
                    "-t",
                    session_name,
                    "-F",
                    "#{pane_current_path}",
                ],
                env=env,
            ).splitlines()
            assert pane_paths
            assert {Path(path).resolve() for path in pane_paths} == {
                runtime_start_directory.resolve()
            }
    finally:
        _run([tmux, "kill-server"], env=env, check=False)


def test_runtime_safe_config_keeps_title_and_hold_in_one_command() -> None:
    expected = {
        "session_name": "demo",
        "windows": [
            {
                "window_name": "ops",
                "panes": [
                    {
                        "shell_command": [
                            "printf '\\033]2;%s\\033\\\\' 'remote host'",
                            "ssh remote",
                        ]
                    }
                ],
            }
        ],
    }

    runtime = _runtime_safe_config(expected, "runtime")

    assert runtime["windows"][0]["panes"][0]["shell_command"] == [
        "printf '\\033]2;%s\\033\\\\' 'remote host'; exec sleep 60"
    ]
