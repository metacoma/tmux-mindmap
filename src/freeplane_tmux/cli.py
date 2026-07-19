from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .errors import SemanticError
from .models import RawNode, RawValidationError

DEFAULT_GRPC_ADDRESS = "127.0.0.1:50051"


def create_live_map(*args, **kwargs):
    from .grpc_client import create_live_map as _create_live_map

    return _create_live_map(*args, **kwargs)


def fetch_live_map(*args, **kwargs):
    from .grpc_client import fetch_live_map as _fetch_live_map

    return _fetch_live_map(*args, **kwargs)


def _slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-._")
    return text or "freeplane"


def _current_program_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).expanduser().resolve())]

    argv0 = (sys.argv[0] if sys.argv else "").strip()
    if argv0:
        resolved = shutil.which(argv0)
        candidate = Path(resolved or argv0).expanduser().resolve()
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return [str(candidate)]

    return [str(Path(sys.executable).expanduser().resolve()), "-m", "freeplane_tmux"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the Freeplane map root to tmuxp YAML and optionally load it."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--create",
        "--create-map",
        dest="create",
        metavar="MAP_NAME",
        help="Create a new unsaved Freeplane map with this name and exit",
    )
    parser.add_argument(
        "--create-terminal",
        help=(
            "Complete GUI terminal command embedded into root script1, for example "
            "'gnome-terminal --', 'xterm -e', or 'kitty --'"
        ),
    )
    parser.add_argument(
        "--addr",
        default=DEFAULT_GRPC_ADDRESS,
        help=f"Freeplane gRPC address (default: {DEFAULT_GRPC_ADDRESS})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="gRPC connection timeout in seconds",
    )
    parser.add_argument("--output-dir", help="Directory for default output files")
    parser.add_argument("--json-out", help="Path for the exported map JSON")
    parser.add_argument("--yaml-out", help="Path for the generated tmuxp YAML")
    parser.add_argument("--load", action="store_true", help="Run tmuxp load")
    parser.add_argument(
        "--detached",
        action="store_true",
        help="Pass --detached to tmuxp load (effective with --load)",
    )
    parser.add_argument(
        "--no-groovy-details",
        action="store_true",
        help="Do not fetch Freeplane detailsText through the Groovy RPC",
    )
    parser.add_argument(
        "--map-json",
        help="Compile a local MindMapToJSON export instead of contacting Freeplane",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def _validate_create_mode(args: argparse.Namespace) -> None:
    incompatible = {
        "--output-dir": args.output_dir,
        "--json-out": args.json_out,
        "--yaml-out": args.yaml_out,
        "--load": args.load,
        "--detached": args.detached,
        "--no-groovy-details": args.no_groovy_details,
        "--map-json": args.map_json,
        "--pretty": args.pretty,
    }
    used = [name for name, value in incompatible.items() if value]
    if used:
        raise ValueError(f"map creation mode cannot be combined with: {', '.join(used)}")


def _validate_mode_combinations(args: argparse.Namespace) -> None:
    if args.create is None and args.create_terminal:
        raise ValueError("--create-terminal can only be used with --create/--create-map")
    if args.detached and not args.load:
        raise ValueError("--detached can only be used with --load")


def _load_map(args: argparse.Namespace) -> tuple[RawNode, dict]:
    if args.map_json:
        raw_data = json.loads(Path(args.map_json).expanduser().read_text(encoding="utf-8"))
    else:
        raw_data = fetch_live_map(
            address=args.addr,
            timeout=args.timeout,
            use_groovy_details=not args.no_groovy_details,
        )
    if not isinstance(raw_data, dict):
        raise ValueError("map JSON root must be an object")
    return RawNode.model_validate(raw_data), raw_data


def _output_paths(args: argparse.Namespace, session_name: str) -> tuple[Path, Path]:
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else Path.cwd()
    slug = _slugify(session_name)
    json_path = (
        Path(args.json_out).expanduser() if args.json_out else output_dir / f"{slug}.map.json"
    )
    yaml_path = (
        Path(args.yaml_out).expanduser() if args.yaml_out else output_dir / f"{slug}.tmuxp.yaml"
    )
    return json_path, yaml_path


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _create_load_command(args: argparse.Namespace) -> list[str]:
    return [
        *_current_program_command(),
        "--addr",
        args.addr,
        "--timeout",
        f"{args.timeout:g}",
        "--load",
    ]


def _run_main(args: argparse.Namespace) -> int:
    _validate_mode_combinations(args)

    if args.create is not None:
        _validate_create_mode(args)
        created_name = create_live_map(
            address=args.addr,
            timeout=args.timeout,
            map_name=args.create,
            terminal_command=args.create_terminal,
            load_command=_create_load_command(args),
        )
        print(created_name)
        return 0

    root, raw_data = _load_map(args)
    from .compiler import MindmapCompiler
    from .emitter import dump_tmuxp_yaml, session_to_tmuxp

    session = MindmapCompiler(root).compile()
    tmuxp_data = session_to_tmuxp(session)
    json_path, yaml_path = _output_paths(args, session.session_name)

    indent = 2 if args.pretty else None
    _write_text(json_path, json.dumps(raw_data, ensure_ascii=False, indent=indent) + "\n")
    _write_text(yaml_path, dump_tmuxp_yaml(tmuxp_data))

    print(yaml_path)
    if args.load:
        from .runtime import run_tmuxp

        run_tmuxp(yaml_path, detached=args.detached)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run_main(args)
    except RawValidationError as exc:
        print(f"RAW VALIDATION ERROR:\n{exc}", file=sys.stderr)
        return 2
    except SemanticError as exc:
        print(f"SEMANTIC VALIDATION ERROR:\n{exc}", file=sys.stderr)
        return 3
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"JSON ERROR: {exc}", file=sys.stderr)
        return 4
    except RuntimeError as exc:
        print(f"RUNTIME ERROR: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
