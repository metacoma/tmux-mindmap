from __future__ import annotations

import os
import sys
from pathlib import Path
from subprocess import CompletedProcess
from types import ModuleType

import pytest

from freeplane_tmux.runtime import run_tmuxp


def _install_fake_tmuxp(monkeypatch, callback) -> None:
    fake_cli_module = ModuleType("tmuxp.cli")
    fake_cli_module.cli = callback  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tmuxp.cli", fake_cli_module)


def _write_workspace(path: Path, *, session_name: str = "demo") -> None:
    path.write_text(f"session_name: {session_name}\nwindows: []\n", encoding="utf-8")


def test_run_tmuxp_uses_bundled_cli_and_replaces_existing_session(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None, str | None]] = []
    tmux_calls: list[tuple[list[str], str | None, str | None]] = []

    def fake_cli(args: list[str]) -> None:
        calls.append((args, os.environ.get("TMUX"), os.environ.get("TMUX_PANE")))

    def fake_run(args: list[str], **kwargs):
        tmux_calls.append((args, os.environ.get("TMUX"), os.environ.get("TMUX_PANE")))
        if args[:3] == ["tmux", "has-session", "-t"]:
            return CompletedProcess(args, 0)
        if args[:3] == ["tmux", "kill-session", "-t"]:
            return CompletedProcess(args, 0)
        raise AssertionError(args)

    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("freeplane_tmux.runtime.subprocess.run", fake_run)
    monkeypatch.setenv("TMUX", "outer")
    monkeypatch.setenv("TMUX_PANE", "%1")
    _install_fake_tmuxp(monkeypatch, fake_cli)

    config = tmp_path / "session.yaml"
    _write_workspace(config)
    run_tmuxp(config, detached=True)

    assert tmux_calls == [
        (["tmux", "has-session", "-t", "demo"], None, None),
        (["tmux", "kill-session", "-t", "demo"], None, None),
    ]
    assert calls == [(["load", "-d", str(config)], None, None)]
    assert os.environ["TMUX"] == "outer"
    assert os.environ["TMUX_PANE"] == "%1"


def test_run_tmuxp_requires_tmux(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="tmux executable not found"):
        run_tmuxp(tmp_path / "session.yaml", detached=False)


def test_run_tmuxp_converts_nonzero_system_exit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(
        "freeplane_tmux.runtime.subprocess.run",
        lambda args, **kwargs: CompletedProcess(args, 1) if args[:3] == ["tmux", "has-session", "-t"] else (_ for _ in ()).throw(AssertionError(args)),
    )
    _install_fake_tmuxp(monkeypatch, lambda args: sys.exit(7))

    config = tmp_path / "session.yaml"
    _write_workspace(config)

    with pytest.raises(RuntimeError, match="exit code 7"):
        run_tmuxp(config, detached=False)


def test_detached_arguments_match_tmuxp_load_parser(tmp_path: Path) -> None:
    import argparse

    from tmuxp.cli.load import create_load_subparser

    parser = create_load_subparser(argparse.ArgumentParser())
    config = tmp_path / "session.yaml"

    args = parser.parse_args(["-d", str(config)])

    assert args.detached is True
    assert args.workspace_files == [str(config)]


def test_run_tmuxp_skips_kill_when_session_does_not_exist(monkeypatch, tmp_path: Path) -> None:
    tmux_calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs):
        tmux_calls.append(args)
        if args[:3] == ["tmux", "has-session", "-t"]:
            return CompletedProcess(args, 1)
        raise AssertionError(args)

    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("freeplane_tmux.runtime.subprocess.run", fake_run)
    _install_fake_tmuxp(monkeypatch, lambda args: None)

    config = tmp_path / "session.yaml"
    _write_workspace(config)

    run_tmuxp(config, detached=False)

    assert tmux_calls == [["tmux", "has-session", "-t", "demo"]]


def test_run_tmuxp_rejects_workspace_without_session_name(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: "/usr/bin/tmux")
    _install_fake_tmuxp(monkeypatch, lambda args: None)

    config = tmp_path / "session.yaml"
    config.write_text("windows: []\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing non-empty session_name"):
        run_tmuxp(config, detached=False)


def test_run_tmuxp_fails_when_kill_session_fails(monkeypatch, tmp_path: Path) -> None:
    def fake_run(args: list[str], **kwargs):
        if args[:3] == ["tmux", "has-session", "-t"]:
            return CompletedProcess(args, 0)
        if args[:3] == ["tmux", "kill-session", "-t"]:
            return CompletedProcess(args, 3)
        raise AssertionError(args)

    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("freeplane_tmux.runtime.subprocess.run", fake_run)
    _install_fake_tmuxp(monkeypatch, lambda args: None)

    config = tmp_path / "session.yaml"
    _write_workspace(config)

    with pytest.raises(RuntimeError, match="kill-session failed"):
        run_tmuxp(config, detached=False)
