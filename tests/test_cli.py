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
