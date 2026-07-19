from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_TERMINAL_COMMAND = "x-terminal-emulator -e"
INSIDE_TERMINAL_FLAG = "--inside-terminal"
INSIDE_TERMINAL_FLAG_LEGACY = "--_freeplane-tmux-inside-terminal"
LAUNCH_GUI_FLAG = "--launch-gui-terminal"
LAUNCH_GUI_FLAG_LEGACY = "--_launch-gui-terminal"
TERMINAL_COMMAND_FLAG = "--terminal"
TERMINAL_PART_FLAG = "--terminal-part"
PAUSE_ON_ERROR = True


def resolve_launch_log_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return Path(runtime_dir) / "freeplane-tmux-launcher.log"


def append_launch_log(message: str) -> Path:
    log_path = resolve_launch_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")
    return log_path


def _command_exists(command_name: str) -> bool:
    if "/" in command_name:
        return Path(command_name).expanduser().is_file() and os.access(command_name, os.X_OK)
    return shutil.which(command_name) is not None


def split_terminal_command(command: str | None) -> list[str]:
    raw_command = command if command is not None else DEFAULT_TERMINAL_COMMAND
    try:
        parts = shlex.split(raw_command)
    except ValueError as exc:
        raise RuntimeError(f"invalid terminal command: {exc}") from exc
    if not parts:
        raise RuntimeError("terminal command must not be empty")
    return parts


def launch_gui_terminal(
    *,
    binary_path: str,
    terminal_command: str | None,
    inner_argv: list[str],
) -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("No GUI display detected (DISPLAY/WAYLAND_DISPLAY is not set)")

    terminal_parts = split_terminal_command(terminal_command)
    terminal_executable = terminal_parts[0]
    if not _command_exists(terminal_executable):
        raise RuntimeError(f"terminal executable not found: {terminal_executable}")

    launch_log = resolve_launch_log_path()
    launch_log.parent.mkdir(parents=True, exist_ok=True)

    command = [*terminal_parts, binary_path, INSIDE_TERMINAL_FLAG, *inner_argv]
    child_env = os.environ.copy()
    removed_tmux = {
        name: child_env.pop(name)
        for name in ("TMUX", "TMUX_PANE")
        if name in child_env
    }
    removed_pyi = {
        name: child_env.pop(name)
        for name in list(child_env)
        if name.startswith("_PYI_")
    }
    child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    append_launch_log(
        "launch_gui_terminal command="
        f"{command!r} cwd={os.getcwd()!r} display={os.environ.get('DISPLAY')!r} "
        f"wayland={os.environ.get('WAYLAND_DISPLAY')!r} removed_tmux={removed_tmux!r} "
        f"removed_pyi={sorted(removed_pyi)!r} "
        f"reset_env={child_env.get('PYINSTALLER_RESET_ENVIRONMENT')!r}"
    )
    with launch_log.open("ab") as log_file:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=child_env,
        )


def pause_for_terminal_exit(exit_code: int) -> None:
    if exit_code == 0 or not PAUSE_ON_ERROR:
        return
    prompt = (
        f"\nfreeplane-tmux exited with status {exit_code}.\n"
        "Press Enter to close this terminal..."
    )
    try:
        if sys.stdin.isatty():
            print(prompt, end="", file=sys.stderr, flush=True)
            input()
    except EOFError:
        return
