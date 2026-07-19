from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from .errors import SemanticError
from .launcher import (
    INSIDE_TERMINAL_FLAG,
    INSIDE_TERMINAL_FLAG_LEGACY,
    LAUNCH_GUI_FLAG,
    LAUNCH_GUI_FLAG_LEGACY,
    TERMINAL_COMMAND_FLAG,
    TERMINAL_PART_FLAG,
    launch_gui_terminal,
    pause_for_terminal_exit,
)
from .models import RawNode, RawValidationError


def create_live_map(*args, **kwargs):
    from .grpc_client import create_live_map as _create_live_map

    return _create_live_map(*args, **kwargs)


def fetch_live_map(*args, **kwargs):
    from .grpc_client import fetch_live_map as _fetch_live_map

    return _fetch_live_map(*args, **kwargs)


def _slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-._")
    return text or "freeplane"


def _current_binary_path() -> str:
    argv0 = (sys.argv[0] if sys.argv else "").strip()
    if argv0:
        expanded = str(Path(argv0).expanduser())
        resolved = shutil.which(expanded)
        if resolved:
            return str(Path(resolved).resolve())
        return str(Path(expanded).resolve())
    return str(Path(sys.executable).expanduser().resolve())


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
            "Shell-style GUI terminal command used by the generated root script1 when "
            "used with --create/--create-map, for example 'gnome-terminal --' or "
            "'xterm -e'"
        ),
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
        help="Compatibility flag; bundled gRPC stubs are always used",
    )
    parser.add_argument(
        "--selected-node-id",
        help="Compatibility flag; ignored because the map root is always the session root",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument(
        LAUNCH_GUI_FLAG,
        LAUNCH_GUI_FLAG_LEGACY,
        dest="launch_gui_terminal",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        INSIDE_TERMINAL_FLAG,
        INSIDE_TERMINAL_FLAG_LEGACY,
        dest="inside_terminal",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        TERMINAL_COMMAND_FLAG,
        dest="terminal_command",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        TERMINAL_PART_FLAG,
        dest="terminal_parts",
        action="append",
        default=[],
        help=argparse.SUPPRESS,
    )
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


def _validate_mode_combinations(args: argparse.Namespace) -> None:
    if args.create is None and args.create_terminal:
        raise ValueError("--create-terminal can only be used with --create/--create-map")


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




@contextmanager
def _system_loader_env():
    tracked = ["LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH"]
    original = {name: os.environ.get(name) for name in tracked}
    try:
        for name in tracked:
            orig_name = f"{name}_ORIG"
            if orig_name in os.environ:
                os.environ[name] = os.environ[orig_name]
            else:
                os.environ.pop(name, None)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

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
        with _system_loader_env():
            tmuxp_cli(command)
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if exit_code:
            raise RuntimeError(f"tmuxp load failed with exit code {exit_code}") from exc


def _normalize_terminal_parts(parts: list[object]) -> list[str]:
    normalized: list[str] = []
    for part in parts:
        if isinstance(part, str):
            normalized.append(part)
            continue
        if isinstance(part, (list, tuple)):
            if not part:
                normalized.append("--")
                continue
            normalized.extend(str(item) for item in part)
            continue
        normalized.append(str(part))
    return normalized


def _resolve_hidden_terminal_command(args: argparse.Namespace) -> str | None:
    if args.terminal_command:
        return args.terminal_command
    terminal_parts = _normalize_terminal_parts(args.terminal_parts)
    if terminal_parts:
        return " ".join(terminal_parts)
    return None


def _rebuild_cli_args(args: argparse.Namespace) -> list[str]:
    rebuilt: list[str] = []
    if args.addr:
        rebuilt.extend(["--addr", args.addr])
    else:
        if args.host:
            rebuilt.extend(["--host", args.host])
        if args.port is not None:
            rebuilt.extend(["--port", str(args.port)])
    if args.timeout != 10.0:
        rebuilt.extend(["--timeout", str(args.timeout)])
    if args.output_dir:
        rebuilt.extend(["--output-dir", args.output_dir])
    if args.json_out:
        rebuilt.extend(["--json-out", args.json_out])
    if args.yaml_out:
        rebuilt.extend(["--yaml-out", args.yaml_out])
    if args.load:
        rebuilt.append("--load")
    if args.detached:
        rebuilt.append("--detached")
    if args.no_groovy_details:
        rebuilt.append("--no-groovy-details")
    if args.map_json:
        rebuilt.extend(["--map-json", args.map_json])
    if args.selected_node_id:
        rebuilt.extend(["--selected-node-id", args.selected_node_id])
    if args.pretty:
        rebuilt.append("--pretty")
    return rebuilt


def _run_main(args: argparse.Namespace) -> int:
    _validate_mode_combinations(args)
    if args.launch_gui_terminal:
        launch_gui_terminal(
            binary_path=_current_binary_path(),
            terminal_command=_resolve_hidden_terminal_command(args),
            inner_argv=_rebuild_cli_args(args),
        )
        return 0

    if args.create is not None:
        _validate_create_mode(args)
        created_name = create_live_map(
            address=_resolve_address(args),
            timeout=args.timeout,
            grpc_stubs_dir=args.grpc_stubs_dir,
            map_name=args.create,
            launcher_binary_path=_current_binary_path(),
            terminal_command=args.create_terminal,
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
    _write_text(
        json_path,
        json.dumps(raw_data, ensure_ascii=False, indent=indent) + "\n",
    )
    _write_text(yaml_path, dump_tmuxp_yaml(tmuxp_data))

    print(yaml_path)
    if args.load:
        _run_tmuxp(yaml_path, detached=args.detached)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code = 0
    try:
        exit_code = _run_main(args)
        return exit_code
    except RawValidationError as exc:
        print(f"RAW VALIDATION ERROR:\n{exc}", file=sys.stderr)
        exit_code = 2
        return exit_code
    except SemanticError as exc:
        print(f"SEMANTIC VALIDATION ERROR:\n{exc}", file=sys.stderr)
        exit_code = 3
        return exit_code
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"JSON ERROR: {exc}", file=sys.stderr)
        exit_code = 4
        return exit_code
    except RuntimeError as exc:
        print(f"RUNTIME ERROR: {exc}", file=sys.stderr)
        exit_code = 5
        return exit_code
    finally:
        if args.inside_terminal:
            pause_for_terminal_exit(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
