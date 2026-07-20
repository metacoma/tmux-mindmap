from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .diagnostics import build_explain_plan, compile_with_diagnostics, explain_text
from .doctor import format_doctor_text, run_doctor
from .errors import SemanticError
from .freeplane_projector import FreeplaneDiagnosticProjector
from .models import RawNode, RawValidationError

DEFAULT_GRPC_ADDRESS = "127.0.0.1:50051"


def create_live_map(*args, **kwargs):
    from .grpc_client import create_live_map as _create_live_map

    return _create_live_map(*args, **kwargs)


def fetch_live_map(*args, **kwargs):
    from .grpc_client import fetch_live_map as _fetch_live_map

    return _fetch_live_map(*args, **kwargs)


def fetch_current_node_id(*args, **kwargs):
    from .grpc_client import fetch_current_node_id as _fetch_current_node_id

    return _fetch_current_node_id(*args, **kwargs)


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
    parser.add_argument(
        "command", nargs="?", choices=["validate", "explain", "doctor", "clear-diagnostics"]
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
        help="Do not fetch Freeplane details through the Groovy RPC",
    )
    parser.add_argument(
        "--map-json",
        help="Use a local MindMapToJSON export instead of contacting Freeplane",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON for validate/explain/doctor",
    )
    dump_group = parser.add_mutually_exclusive_group()
    dump_group.add_argument(
        "--dump",
        action="store_true",
        help="Print the complete current map as JSON to stdout and exit",
    )
    dump_group.add_argument(
        "--dump-from-node",
        action="store_true",
        help="Print the selected node and its complete subtree as JSON to stdout and exit",
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
        "--dump": args.dump,
        "--dump-from-node": args.dump_from_node,
        "--pretty": args.pretty,
        "command": args.command,
    }
    used = [name for name, value in incompatible.items() if value]
    if used:
        raise ValueError(f"map creation mode cannot be combined with: {', '.join(used)}")


def _validate_mode_combinations(args: argparse.Namespace) -> None:
    if args.create is None and args.create_terminal:
        raise ValueError("--create-terminal can only be used with --create/--create-map")
    if args.detached and not args.load:
        raise ValueError("--detached can only be used with --load")
    if args.dump_from_node and args.map_json:
        raise ValueError("--dump-from-node requires a live Freeplane connection")

    if args.command is not None and (args.dump or args.dump_from_node or args.load or args.create):
        raise ValueError(f"{args.command} cannot be combined with dump/create/load modes")

    if args.dump or args.dump_from_node:
        incompatible = {
            "--output-dir": args.output_dir,
            "--json-out": args.json_out,
            "--yaml-out": args.yaml_out,
            "--load": args.load,
            "--detached": args.detached,
        }
        used = [name for name, value in incompatible.items() if value]
        if used:
            raise ValueError(f"dump mode cannot be combined with: {', '.join(used)}")


def _load_raw_map(args: argparse.Namespace) -> dict:
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
    return raw_data


def _load_map(args: argparse.Namespace) -> tuple[RawNode, dict]:
    raw_data = _load_raw_map(args)
    return RawNode.model_validate(raw_data), raw_data


def _find_subtree(node: dict, node_id: str) -> dict | None:
    if str(node.get("id", "")) == node_id:
        return node
    for child in node.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        found = _find_subtree(child, node_id)
        if found is not None:
            return found
    return None


def _dump_json(value: dict, *, pretty: bool) -> None:
    indent = 2 if pretty else None
    print(json.dumps(value, ensure_ascii=False, indent=indent))


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


def _maybe_project_diagnostics(args: argparse.Namespace, diagnostics) -> None:
    if args.map_json:
        return
    projector = FreeplaneDiagnosticProjector(address=args.addr, timeout=args.timeout)
    projector.apply(list(diagnostics))


def _handle_validate(args: argparse.Namespace) -> int:
    root, _raw_data = _load_map(args)
    result = compile_with_diagnostics(root)
    if not args.map_json:
        _maybe_project_diagnostics(args, result.diagnostics)
    if args.json:
        print(
            json.dumps(result.to_json_dict(), ensure_ascii=False, indent=2 if args.pretty else None)
        )
    else:
        for diagnostic in result.diagnostics:
            location = f" [{diagnostic.node_path}]" if diagnostic.node_path else ""
            print(
                f"{diagnostic.severity.upper()} {diagnostic.code}: {diagnostic.message}{location}",
                file=sys.stdout,
            )
    return 0 if result.ok else 1


def _handle_explain(args: argparse.Namespace) -> int:
    root, _raw_data = _load_map(args)
    result = compile_with_diagnostics(root)
    if not result.ok or result.session is None:
        if args.json:
            print(
                json.dumps(
                    result.to_json_dict(), ensure_ascii=False, indent=2 if args.pretty else None
                )
            )
        else:
            for diagnostic in result.diagnostics:
                print(f"{diagnostic.severity.upper()} {diagnostic.code}: {diagnostic.message}")
        return 1
    plan = build_explain_plan(root, result.session)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2 if args.pretty else None))
    else:
        print(explain_text(plan), end="")
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(address=args.addr, timeout=args.timeout)
    if args.json:
        print(
            json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2 if args.pretty else None)
        )
    else:
        print(format_doctor_text(report), end="")
    return 0 if report.ok else 1


def _handle_clear_diagnostics(args: argparse.Namespace) -> int:
    if args.map_json:
        raise ValueError("clear-diagnostics requires a live Freeplane connection")
    projector = FreeplaneDiagnosticProjector(address=args.addr, timeout=args.timeout)
    projector.clear()
    if not args.json:
        print("Cleared tmux-mindmap diagnostics")
    else:
        print(json.dumps({"ok": True, "cleared": True}, ensure_ascii=False))
    return 0


def _run_main(args: argparse.Namespace) -> int:
    _validate_mode_combinations(args)

    if args.command == "validate":
        return _handle_validate(args)
    if args.command == "explain":
        return _handle_explain(args)
    if args.command == "doctor":
        return _handle_doctor(args)
    if args.command == "clear-diagnostics":
        return _handle_clear_diagnostics(args)

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

    if args.dump or args.dump_from_node:
        selected_node_id = None
        if args.dump_from_node:
            selected_node_id = fetch_current_node_id(address=args.addr, timeout=args.timeout)

        raw_data = _load_raw_map(args)
        if selected_node_id is not None:
            subtree = _find_subtree(raw_data, selected_node_id)
            if subtree is None:
                raise RuntimeError(
                    f"selected Freeplane node {selected_node_id!r} is absent from the map export"
                )
            raw_data = subtree
        _dump_json(raw_data, pretty=args.pretty)
        return 0

    root, raw_data = _load_map(args)
    from .emitter import dump_tmuxp_yaml, session_to_tmuxp

    result = compile_with_diagnostics(root)
    if not result.ok or result.session is None:
        for diagnostic in result.diagnostics:
            print(
                f"{diagnostic.severity.upper()} {diagnostic.code}: {diagnostic.message}",
                file=sys.stderr,
            )
        return 1

    session = result.session
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
    modern_mode = args.command in {"validate", "explain", "doctor", "clear-diagnostics"}
    try:
        return _run_main(args)
    except RawValidationError as exc:
        print(f"RAW VALIDATION ERROR:\n{exc}", file=sys.stderr)
        return 2
    except SemanticError as exc:
        print(f"SEMANTIC VALIDATION ERROR:\n{exc}", file=sys.stderr)
        return 2 if modern_mode else 3
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"JSON ERROR: {exc}", file=sys.stderr)
        return 2 if modern_mode else 4
    except RuntimeError as exc:
        print(f"RUNTIME ERROR: {exc}", file=sys.stderr)
        return 2 if modern_mode else 5


if __name__ == "__main__":
    raise SystemExit(main())
