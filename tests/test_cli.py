from __future__ import annotations

import json
from pathlib import Path

import pytest

from freeplane_tmux.cli import build_parser


def test_current_cli_arguments_are_accepted() -> None:
    args = build_parser().parse_args(
        [
            "--addr",
            "127.0.0.1:50051",
            "--timeout",
            "3",
            "--output-dir",
            "out",
            "--json-out",
            "map.json",
            "--yaml-out",
            "session.yaml",
            "--load",
            "--detached",
            "--no-groovy-details",
        ]
    )
    assert args.yaml_out == "session.yaml"
    assert args.load is True
    assert args.detached is True


@pytest.mark.parametrize(
    "legacy_args",
    [
        ["--host", "localhost"],
        ["--port", "50052"],
        ["--tmuxp-out", "session.yaml"],
        ["--grpc-stubs-dir", "/tmp/stubs"],
        ["--selected-node-id", "node"],
        ["--launch-gui-terminal"],
        ["--inside-terminal"],
    ],
)
def test_transitional_cli_arguments_are_removed(legacy_args: list[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(legacy_args)


def test_create_aliases_and_terminal_are_accepted() -> None:
    for flag in ("--create", "--create-map"):
        args = build_parser().parse_args(
            [
                "--addr",
                "freeplane.example:50052",
                flag,
                "Operations",
                "--create-terminal",
                "gnome-terminal --",
            ]
        )
        assert args.addr == "freeplane.example:50052"
        assert args.create == "Operations"
        assert args.create_terminal == "gnome-terminal --"


def test_main_creates_map_with_final_load_command(monkeypatch, capsys) -> None:
    from freeplane_tmux.cli import main

    calls: list[dict[str, object]] = []

    def fake_create_live_map(**kwargs: object) -> str:
        calls.append(kwargs)
        return "Operations"

    monkeypatch.setattr("freeplane_tmux.cli.create_live_map", fake_create_live_map)
    monkeypatch.setattr(
        "freeplane_tmux.cli._current_program_command",
        lambda: ["/tmp/freeplane-tmux"],
    )

    result = main(
        [
            "--addr",
            "freeplane.example:50052",
            "--timeout",
            "3.5",
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
            "timeout": 3.5,
            "map_name": "Operations",
            "terminal_command": "gnome-terminal --",
            "load_command": [
                "/tmp/freeplane-tmux",
                "--addr",
                "freeplane.example:50052",
                "--timeout",
                "3.5",
                "--load",
            ],
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


def test_detached_requires_load(capsys) -> None:
    from freeplane_tmux.cli import main

    result = main(["--detached", "--map-json", "missing.json"])

    assert result == 4
    assert "--detached can only be used with --load" in capsys.readouterr().err


def test_current_program_command_prefers_executable_entrypoint(monkeypatch, tmp_path: Path) -> None:
    from freeplane_tmux.cli import _current_program_command

    binary = tmp_path / "freeplane-tmux"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    monkeypatch.delattr("freeplane_tmux.cli.sys.frozen", raising=False)
    monkeypatch.setattr("freeplane_tmux.cli.sys.argv", ["freeplane-tmux"])
    monkeypatch.setattr("freeplane_tmux.cli.shutil.which", lambda name: str(binary))

    assert _current_program_command() == [str(binary.resolve())]


def test_current_program_command_falls_back_to_python_module(monkeypatch, tmp_path: Path) -> None:
    from freeplane_tmux.cli import _current_program_command

    non_executable = tmp_path / "runner.py"
    non_executable.write_text("", encoding="utf-8")

    monkeypatch.delattr("freeplane_tmux.cli.sys.frozen", raising=False)
    monkeypatch.setattr("freeplane_tmux.cli.sys.argv", [str(non_executable)])
    monkeypatch.setattr("freeplane_tmux.cli.shutil.which", lambda name: None)
    monkeypatch.setattr("freeplane_tmux.cli.sys.executable", "/usr/bin/python3")

    assert _current_program_command() == [
        str(Path("/usr/bin/python3").resolve()),
        "-m",
        "freeplane_tmux",
    ]


def test_load_compiles_emits_and_runs_tmuxp(monkeypatch, tmp_path: Path, capsys) -> None:
    from freeplane_tmux.cli import main

    raw_map = {
        "id": "root",
        "text": "demo",
        "children": [
            {
                "id": "window",
                "text": "hello-win",
                "tags": ["WINDOW"],
                "children": [
                    {
                        "id": "command",
                        "text": "echo hello world",
                    }
                ],
            }
        ],
    }
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(raw_map), encoding="utf-8")
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        "freeplane_tmux.runtime.run_tmuxp",
        lambda path, *, detached: calls.append((path, detached)),
    )

    result = main(
        [
            "--map-json",
            str(map_path),
            "--output-dir",
            str(tmp_path),
            "--load",
            "--detached",
        ]
    )

    yaml_path = tmp_path / "demo.tmuxp.yaml"
    assert result == 0
    assert yaml_path.is_file()
    assert "echo hello world" in yaml_path.read_text(encoding="utf-8")
    assert calls == [(yaml_path, True)]
    assert capsys.readouterr().out == f"{yaml_path}\n"


def test_dump_arguments_are_accepted() -> None:
    args = build_parser().parse_args(["--dump", "--pretty"])
    assert args.dump is True
    assert args.dump_from_node is False
    assert args.pretty is True

    args = build_parser().parse_args(["--dump-from-node"])
    assert args.dump is False
    assert args.dump_from_node is True


def test_dump_prints_local_map_without_compiling(tmp_path: Path, capsys) -> None:
    from freeplane_tmux.cli import main

    raw_map = {
        "id": "root",
        "text": "debug-map",
        "children": [{"id": "child", "text": "child", "children": []}],
    }
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(raw_map), encoding="utf-8")

    result = main(["--map-json", str(map_path), "--dump", "--pretty"])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == raw_map
    assert not (tmp_path / "debug-map.map.json").exists()
    assert not (tmp_path / "debug-map.tmuxp.yaml").exists()


def test_dump_from_node_prints_selected_subtree(monkeypatch, capsys) -> None:
    from freeplane_tmux.cli import main

    raw_map = {
        "id": "root",
        "text": "debug-map",
        "children": [
            {
                "id": "selected",
                "text": "selected",
                "children": [{"id": "leaf", "text": "leaf", "children": []}],
            },
            {"id": "other", "text": "other", "children": []},
        ],
    }
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(
        "freeplane_tmux.cli.fetch_current_node_id",
        lambda *, address, timeout: calls.append((address, timeout)) or "selected",
    )
    monkeypatch.setattr("freeplane_tmux.cli.fetch_live_map", lambda **kwargs: raw_map)

    result = main(
        [
            "--addr",
            "freeplane.example:50052",
            "--timeout",
            "3.5",
            "--dump-from-node",
        ]
    )

    assert result == 0
    assert calls == [("freeplane.example:50052", 3.5)]
    assert json.loads(capsys.readouterr().out) == raw_map["children"][0]


def test_dump_from_node_rejects_local_map(capsys) -> None:
    from freeplane_tmux.cli import main

    result = main(["--map-json", "map.json", "--dump-from-node"])

    assert result == 4
    assert "requires a live Freeplane connection" in capsys.readouterr().err


def test_dump_mode_rejects_file_and_load_options(capsys) -> None:
    from freeplane_tmux.cli import main

    result = main(["--dump", "--json-out", "map.json", "--load"])

    assert result == 4
    error = capsys.readouterr().err
    assert "dump mode cannot be combined with" in error
    assert "--json-out" in error
    assert "--load" in error
