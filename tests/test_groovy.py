from __future__ import annotations

import pytest

from freeplane_tmux.groovy import (
    DEFAULT_TERMINAL_COMMAND,
    build_create_map_script,
    build_root_script,
    parse_terminal_command,
)


def test_parse_terminal_command_uses_default() -> None:
    assert parse_terminal_command(None) == DEFAULT_TERMINAL_COMMAND.split()


def test_parse_terminal_command_respects_shell_quoting() -> None:
    assert parse_terminal_command('gnome-terminal --title "Operations" --') == [
        "gnome-terminal",
        "--title",
        "Operations",
        "--",
    ]


def test_parse_terminal_command_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="invalid create-terminal"):
        parse_terminal_command('xterm -e "unterminated')


def test_root_script_builds_final_background_terminal_command() -> None:
    script = build_root_script(
        terminal_command="gnome-terminal --",
        load_command=[
            "/opt/freeplane-tmux/bin/freeplane-tmux",
            "--addr",
            "127.0.0.1:50051",
            "--load",
        ],
    )

    assert "// @ExecutionModes({ON_SELECTED_NODE})" in script
    assert "def terminalCommand = ['gnome-terminal', '--']" in script
    assert (
        "def loadCommand = ['/opt/freeplane-tmux/bin/freeplane-tmux', '--addr', "
        "'127.0.0.1:50051', '--load']"
    ) in script
    assert "cmd.addAll(terminalCommand" in script
    assert "cmd.addAll(loadCommand" in script
    assert "def pb = new ProcessBuilder(cmd)" in script
    assert "pb.start()" in script
    assert "waitFor" not in script
    assert 'childEnvironment.remove("TMUX")' in script
    assert 'childEnvironment.remove("TMUX_PANE")' in script
    assert "DISPLAY" in script
    assert "WAYLAND_DISPLAY" in script


def test_root_script_has_no_transitional_launcher_path() -> None:
    script = build_root_script(
        terminal_command="xterm -e",
        load_command=["/usr/local/bin/freeplane-tmux", "--load"],
    )

    assert ".sh" not in script
    assert "--launch-gui-terminal" not in script
    assert "--_launch-gui-terminal" not in script
    assert "--inside-terminal" not in script
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in script
    assert "_PYI_" not in script


def test_create_map_script_sets_script_and_starter_structure() -> None:
    root_script = build_root_script(
        terminal_command="kitty --",
        load_command=["/usr/local/bin/freeplane-tmux", "--load"],
    )
    script = build_create_map_script(
        map_name='ops "map"\nnewMap.name = "injected"',
        root_script=root_script,
    )

    assert script.count("newMap.name = mapName") == 1
    assert '\nnewMap.name = "injected"' not in script
    assert "newMap.root['script1'] = rootScript" in script
    assert 'def helloWindow = newMap.root.createChild("hello-win")' in script
    assert 'def helloCommand = helloWindow.createChild("echo hello world")' in script
    assert 'helloWindow.tags.add("WINDOW")' in script
    assert "rootScriptBase64" not in script
    assert "launcherScript" not in script
