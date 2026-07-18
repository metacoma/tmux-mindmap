from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from pydantic import ValidationError

from .compiler import MindmapCompiler
from .emitter import dump_tmuxp_yaml, session_to_tmuxp
from .errors import SemanticError
from .grpc_client import GrpcClientError, create_live_map, fetch_live_map
from .models import RawNode


def _slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-._")
    return text or "freeplane"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the Freeplane map root to tmuxp YAML and optionally load it."
    )
    parser.add_argument(
        "map_name",
        nargs="?",
        help="Create a new unsaved Freeplane map with this name and exit",
    )
    parser.add_argument("--addr", help="Freeplane gRPC address, e.g. 127.0.0.1:50051")
    parser.add_argument("--host", help="Compatibility alias for the gRPC host")
    parser.add_argument("--port", type=int, help="Compatibility alias for the gRPC port")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="gRPC connection timeout in seconds",
    )
    parser.add_argument("--output-dir", help="Directory for default output files")
    parser.add_argument("--json-out", help="Path for the exported map JSON")
    parser.add_argument(
        "--yaml-out",
        "--tmuxp-out",
        dest="yaml_out",
        help="Path for the generated tmuxp YAML",
    )
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
    parser.add_argument(
        "--grpc-stubs-dir",
        type=Path,
        help="Directory containing freeplane_pb2.py and freeplane_pb2_grpc.py",
    )
    parser.add_argument(
        "--selected-node-id",
        help="Compatibility flag; ignored because the map root is always the session root",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def _resolve_address(args: argparse.Namespace) -> str:
    if args.addr:
        return args.addr
    return f"{args.host or '127.0.0.1'}:{args.port or 50051}"


def _validate_create_mode(args: argparse.Namespace) -> None:
    incompatible = {
        "--output-dir": args.output_dir,
        "--json-out": args.json_out,
        "--yaml-out/--tmuxp-out": args.yaml_out,
        "--load": args.load,
        "--detached": args.detached,
        "--no-groovy-details": args.no_groovy_details,
        "--map-json": args.map_json,
        "--selected-node-id": args.selected_node_id,
        "--pretty": args.pretty,
    }
    used = [name for name, value in incompatible.items() if value]
    if used:
        flags = ", ".join(used)
        raise ValueError(f"map creation mode cannot be combined with: {flags}")


def _load_map(args: argparse.Namespace) -> tuple[RawNode, dict]:
    if args.map_json:
        raw_data = json.loads(Path(args.map_json).expanduser().read_text(encoding="utf-8"))
    else:
        raw_data = fetch_live_map(
            address=_resolve_address(args),
            timeout=args.timeout,
            use_groovy_details=not args.no_groovy_details,
            grpc_stubs_dir=args.grpc_stubs_dir,
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


def _run_tmuxp(path: Path, *, detached: bool) -> None:
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
        tmuxp_cli(command)
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if exit_code:
            raise RuntimeError(f"tmuxp load failed with exit code {exit_code}") from exc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.map_name is not None:
            _validate_create_mode(args)
            created_name = create_live_map(
                address=_resolve_address(args),
                timeout=args.timeout,
                grpc_stubs_dir=args.grpc_stubs_dir,
                map_name=args.map_name,
            )
            print(created_name)
            return 0

        root, raw_data = _load_map(args)
        session = MindmapCompiler(root).compile()
        tmuxp_data = session_to_tmuxp(session)
        json_path, yaml_path = _output_paths(args, session.session_name)

        indent = 2 if args.pretty else None
        _write_text(
            json_path,
            json.dumps(raw_data, ensure_ascii=False, indent=indent) + "\n",
        )
        _write_text(yaml_path, dump_tmuxp_yaml(tmuxp_data))

        print(yaml_path)
        if args.load:
            _run_tmuxp(yaml_path, detached=args.detached)
        return 0
    except ValidationError as exc:
        print(f"RAW VALIDATION ERROR:\n{exc}", file=sys.stderr)
        return 2
    except SemanticError as exc:
        print(f"SEMANTIC VALIDATION ERROR:\n{exc}", file=sys.stderr)
        return 3
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"JSON ERROR: {exc}", file=sys.stderr)
        return 4
    except (GrpcClientError, RuntimeError) as exc:
        print(f"RUNTIME ERROR: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
