from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _system_loader_environment():
    """Expose system loader paths while tmuxp starts external system programs."""

    tracked = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH")
    original = {name: os.environ.get(name) for name in tracked}
    try:
        for name in tracked:
            original_name = f"{name}_ORIG"
            if original_name in os.environ:
                os.environ[name] = os.environ[original_name]
            else:
                os.environ.pop(name, None)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def _outside_tmux_environment():
    tracked = ("TMUX", "TMUX_PANE")
    original = {name: os.environ.get(name) for name in tracked}
    try:
        for name in tracked:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_tmuxp(path: Path, *, detached: bool) -> None:
    if shutil.which("tmux") is None:
        raise RuntimeError("tmux executable not found in PATH")

    try:
        from tmuxp.cli import cli as tmuxp_cli
    except ImportError as exc:
        raise RuntimeError("bundled tmuxp runtime is unavailable") from exc

    command = ["load"]
    if detached:
        command.append("--detached")
    command.append(str(path))

    try:
        with _system_loader_environment(), _outside_tmux_environment():
            tmuxp_cli(command)
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if exit_code:
            raise RuntimeError(f"tmuxp load failed with exit code {exit_code}") from exc
