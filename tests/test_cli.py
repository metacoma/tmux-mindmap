from freeplane_tmux.cli import build_parser


def test_legacy_cli_arguments_are_accepted() -> None:
    args = build_parser().parse_args(
        [
            "--addr",
            "127.0.0.1:50051",
            "--host",
            "localhost",
            "--port",
            "50052",
            "--timeout",
            "3",
            "--output-dir",
            "out",
            "--json-out",
            "map.json",
            "--tmuxp-out",
            "session.yaml",
            "--load",
            "--detached",
            "--no-groovy-details",
        ]
    )
    assert args.yaml_out == "session.yaml"
    assert args.load is True
    assert args.detached is True


def test_run_tmuxp_uses_bundled_cli(monkeypatch, tmp_path) -> None:
    import sys
    from types import ModuleType

    from freeplane_tmux.cli import _run_tmuxp

    calls: list[list[str]] = []
    fake_cli_module = ModuleType("tmuxp.cli")
    fake_cli_module.cli = lambda args: calls.append(args)  # type: ignore[attr-defined]

    monkeypatch.setattr("freeplane_tmux.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setitem(sys.modules, "tmuxp.cli", fake_cli_module)

    config = tmp_path / "session.yaml"
    _run_tmuxp(config, detached=True)

    assert calls == [["load", "--detached", str(config)]]


def test_run_tmuxp_requires_tmux(monkeypatch, tmp_path) -> None:
    import pytest

    from freeplane_tmux.cli import _run_tmuxp

    monkeypatch.setattr("freeplane_tmux.cli.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="tmux executable not found"):
        _run_tmuxp(tmp_path / "session.yaml", detached=False)
