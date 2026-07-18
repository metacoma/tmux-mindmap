from __future__ import annotations

import base64
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_TERMINAL_COMMAND = "x-terminal-emulator -e"
INSIDE_TERMINAL_FLAG = "--_freeplane-tmux-inside-terminal"
LAUNCH_GUI_FLAG = "--_launch-gui-terminal"
TERMINAL_PART_FLAG = "--terminal-part"
TERMINAL_COMMAND_B64_FLAG = "--terminal-command-b64"
PAUSE_ON_ERROR = True


def resolve_launch_log_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return Path(runtime_dir) / "freeplane-tmux-launcher.log"


def _command_exists(command_name: str) -> bool:
    if "/" in command_name:
        return Path(command_name).expanduser().is_file() and os.access(command_name, os.X_OK)
    return shutil.which(command_name) is not None




def encode_terminal_command(command: str | None) -> str:
    raw = command if command is not None else ""
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_terminal_command(encoded: str) -> str:
    try:
        return base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("invalid encoded terminal command") from exc

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
    with launch_log.open("ab") as log_file:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
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
