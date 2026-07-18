import pytest

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
    from freeplane_tmux.cli import _run_tmuxp

    monkeypatch.setattr("freeplane_tmux.cli.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="tmux executable not found"):
        _run_tmuxp(tmp_path / "session.yaml", detached=False)


def test_create_mode_is_accepted() -> None:
    args = build_parser().parse_args(
        [
            "--host",
            "freeplane.example",
            "--port",
            "50052",
            "--create-map",
            "Operations",
            "--create-terminal",
            "gnome-terminal --",
        ]
    )
    assert args.host == "freeplane.example"
    assert args.port == 50052
    assert args.create == "Operations"
    assert args.create_terminal == "gnome-terminal --"


def test_main_creates_map_and_exits(monkeypatch, capsys) -> None:
    from freeplane_tmux.cli import main

    calls: list[dict[str, object]] = []

    def fake_create_live_map(**kwargs: object) -> str:
        calls.append(kwargs)
        return "Operations"

    monkeypatch.setattr("freeplane_tmux.cli.create_live_map", fake_create_live_map)
    monkeypatch.setattr("freeplane_tmux.cli._current_binary_path", lambda: "/tmp/freeplane-bin")

    result = main(
        [
            "--host",
            "freeplane.example",
            "--port",
            "50052",
            "--create-map",
            "Operations",
            "--create-terminal",
            "gnome-terminal --",
        ]
    )

    assert result == 0
    assert calls == [
        {
            "address": "freeplane.example:50052",
            "timeout": 10.0,
            "grpc_stubs_dir": None,
            "map_name": "Operations",
            "launcher_binary_path": "/tmp/freeplane-bin",
            "terminal_command": "gnome-terminal --",
        }
    ]
    assert capsys.readouterr().out == "Operations\n"


def test_create_map_mode_rejects_tmux_options(monkeypatch, capsys) -> None:
    from freeplane_tmux.cli import main

    monkeypatch.setattr(
        "freeplane_tmux.cli.create_live_map",
        lambda **kwargs: pytest.fail("create_live_map must not be called"),
    )

    result = main(["--load", "--create", "Operations"])

    assert result == 4
    assert "map creation mode cannot be combined with: --load" in capsys.readouterr().err


def test_create_terminal_requires_create(capsys) -> None:
    from freeplane_tmux.cli import main

    result = main(["--create-terminal", "gnome-terminal --"])

    assert result == 4
    assert "--create-terminal can only be used" in capsys.readouterr().err


def test_current_binary_path_prefers_path_resolution(monkeypatch, tmp_path) -> None:
    from freeplane_tmux.cli import _current_binary_path

    binary = tmp_path / "freeplane-tmux"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    monkeypatch.setattr("freeplane_tmux.cli.sys.argv", ["freeplane-tmux"])
    monkeypatch.setattr("freeplane_tmux.cli.shutil.which", lambda name: str(binary))

    assert _current_binary_path() == str(binary.resolve())
