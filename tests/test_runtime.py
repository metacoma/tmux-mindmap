from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from freeplane_tmux.runtime import run_tmuxp


def _install_fake_tmuxp(monkeypatch, callback) -> None:
    fake_cli_module = ModuleType("tmuxp.cli")
    fake_cli_module.cli = callback  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tmuxp.cli", fake_cli_module)


def test_run_tmuxp_uses_bundled_cli(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None, str | None]] = []

    def fake_cli(args: list[str]) -> None:
        calls.append((args, os.environ.get("TMUX"), os.environ.get("TMUX_PANE")))

    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setenv("TMUX", "outer")
    monkeypatch.setenv("TMUX_PANE", "%1")
    _install_fake_tmuxp(monkeypatch, fake_cli)

    config = tmp_path / "session.yaml"
    run_tmuxp(config, detached=True)

    assert calls == [(["load", "-d", str(config)], None, None)]
    assert os.environ["TMUX"] == "outer"
    assert os.environ["TMUX_PANE"] == "%1"


def test_run_tmuxp_requires_tmux(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="tmux executable not found"):
        run_tmuxp(tmp_path / "session.yaml", detached=False)


def test_run_tmuxp_converts_nonzero_system_exit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("freeplane_tmux.runtime.shutil.which", lambda name: "/usr/bin/tmux")
    _install_fake_tmuxp(monkeypatch, lambda args: sys.exit(7))

    with pytest.raises(RuntimeError, match="exit code 7"):
        run_tmuxp(tmp_path / "session.yaml", detached=False)


def test_detached_arguments_match_tmuxp_load_parser(tmp_path: Path) -> None:
    import argparse

    from tmuxp.cli.load import create_load_subparser

    parser = create_load_subparser(argparse.ArgumentParser())
    config = tmp_path / "session.yaml"

    args = parser.parse_args(["-d", str(config)])

    assert args.detached is True
    assert args.workspace_files == [str(config)]
