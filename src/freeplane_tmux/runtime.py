from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import yaml


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


def _session_name_from_workspace(path: Path) -> str:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"failed to read tmuxp workspace: {path}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"tmuxp workspace must be a mapping: {path}")

    session_name = raw.get("session_name")
    if not isinstance(session_name, str) or not session_name.strip():
        raise RuntimeError(f"tmuxp workspace missing non-empty session_name: {path}")
    return session_name


def _kill_existing_session(session_name: str) -> None:
    with _outside_tmux_environment():
        probe = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 1:
            return
        if probe.returncode != 0:
            raise RuntimeError(
                "tmux has-session failed for session "
                f"{session_name!r} with exit code {probe.returncode}"
            )

        kill = subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)
        if kill.returncode != 0:
            raise RuntimeError(
                "tmux kill-session failed for session "
                f"{session_name!r} with exit code {kill.returncode}"
            )


def run_tmuxp(path: Path, *, detached: bool) -> None:
    if shutil.which("tmux") is None:
        raise RuntimeError("tmux executable not found in PATH")

    try:
        from tmuxp.cli import cli as tmuxp_cli
    except ImportError as exc:
        raise RuntimeError("bundled tmuxp runtime is unavailable") from exc

    session_name = _session_name_from_workspace(path)
    _kill_existing_session(session_name)

    command = ["load"]
    if detached:
        command.append("-d")
    command.append(str(path))

    try:
        with _system_loader_environment(), _outside_tmux_environment():
            tmuxp_cli(command)
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if exit_code:
            raise RuntimeError(f"tmuxp load failed with exit code {exit_code}") from exc
