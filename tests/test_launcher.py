from __future__ import annotations

from pathlib import Path

import pytest

from freeplane_tmux.cli import build_parser, main
from freeplane_tmux.launcher import (
    DEFAULT_TERMINAL_COMMAND,
    INSIDE_TERMINAL_FLAG,
    LAUNCH_GUI_FLAG,
    TERMINAL_COMMAND_FLAG,
    launch_gui_terminal,
    split_terminal_command,
)


def test_split_terminal_command_uses_default() -> None:
    assert split_terminal_command(None) == DEFAULT_TERMINAL_COMMAND.split()


def test_split_terminal_command_respects_shell_syntax() -> None:
    assert split_terminal_command('gnome-terminal --title "Ops" --') == [
        'gnome-terminal',
        '--title',
        'Ops',
        '--',
    ]


def test_launch_gui_terminal_spawns_expected_command(monkeypatch, tmp_path: Path) -> None:
    binary_path = tmp_path / "freeplane-tmux"
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
    binary_path.chmod(0o755)

    popen_calls: list[dict[str, object]] = []

    class DummyPopen:
        def __init__(self, command, **kwargs):
            popen_calls.append({"command": command, **kwargs})

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("freeplane_tmux.launcher.subprocess.Popen", DummyPopen)
    monkeypatch.setattr("freeplane_tmux.launcher.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    launch_gui_terminal(
        binary_path=str(binary_path),
        terminal_command="xterm -e",
        inner_argv=["--load", "--pretty"],
    )

    assert len(popen_calls) == 1
    assert popen_calls[0]["command"] == [
        "xterm",
        "-e",
        str(binary_path),
        INSIDE_TERMINAL_FLAG,
        "--load",
        "--pretty",
    ]
    assert popen_calls[0]["start_new_session"] is True
    child_env = popen_calls[0]["env"]
    assert child_env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_launch_gui_terminal_requires_gui(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(RuntimeError, match="No GUI display"):
        launch_gui_terminal(
            binary_path=str(tmp_path / "freeplane-tmux"),
            terminal_command="xterm -e",
            inner_argv=["--load"],
        )




def test_normalize_terminal_parts_handles_python310_dash_dash() -> None:
    from freeplane_tmux.cli import _normalize_terminal_parts

    assert _normalize_terminal_parts(["gnome-terminal", []]) == ["gnome-terminal", "--"]

def test_hidden_launch_mode_rebuilds_load_args(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    monkeypatch.setattr("freeplane_tmux.cli._current_binary_path", lambda: "/tmp/freeplane-tmux")
    monkeypatch.setattr(
        "freeplane_tmux.cli.launch_gui_terminal",
        lambda **kwargs: captured.append(kwargs),
    )

    result = main([
        LAUNCH_GUI_FLAG,
        f"{TERMINAL_COMMAND_FLAG}=gnome-terminal --",
        "--load",
        "--detached",
        "--pretty",
    ])

    assert result == 0
    assert captured == [
        {
            "binary_path": "/tmp/freeplane-tmux",
            "terminal_command": "gnome-terminal --",
            "inner_argv": ["--load", "--detached", "--pretty"],
        }
    ]


def test_hidden_launch_mode_legacy_terminal_parts_still_work(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    monkeypatch.setattr("freeplane_tmux.cli._current_binary_path", lambda: "/tmp/freeplane-tmux")
    monkeypatch.setattr(
        "freeplane_tmux.cli.launch_gui_terminal",
        lambda **kwargs: captured.append(kwargs),
    )

    result = main([
        "--_launch-gui-terminal",
        "--terminal-part=gnome-terminal",
        "--terminal-part=--",
        "--load",
    ])

    assert result == 0
    assert captured == [
        {
            "binary_path": "/tmp/freeplane-tmux",
            "terminal_command": "gnome-terminal --",
            "inner_argv": ["--load"],
        }
    ]


def test_parser_accepts_hidden_launcher_flags() -> None:
    args = build_parser().parse_args([
        LAUNCH_GUI_FLAG,
        INSIDE_TERMINAL_FLAG,
        f"{TERMINAL_COMMAND_FLAG}=xterm -e",
    ])
    assert args.launch_gui_terminal is True
    assert args.inside_terminal is True
    assert args.terminal_command == "xterm -e"
