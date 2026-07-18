from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .models import RawNode
from .text import sanitize_details_text


class GrpcClientError(RuntimeError):
    pass


def _extract_json_value(value: str) -> Any | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for start, character in enumerate(text):
        if character not in "[{":
            continue
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end].strip())
            except json.JSONDecodeError:
                continue
    return None


def _candidate_stub_directories(explicit: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())

    configured = os.environ.get("FREEPLANE_GRPC_PYTHON_PATH")
    if configured:
        candidates.append(Path(configured).expanduser())

    cwd = Path.cwd()
    package_dir = Path(__file__).resolve().parent
    project_root = package_dir.parents[1]
    launcher_dir = Path(sys.argv[0]).expanduser().resolve().parent
    candidates.extend(
        [
            cwd,
            cwd / "grpc" / "python",
            cwd / "freeplane_plugin_grpc" / "grpc" / "python",
            launcher_dir,
            package_dir,
            project_root,
            project_root / "grpc" / "python",
        ]
    )
    return candidates


def _load_stubs(explicit: Path | None) -> tuple[ModuleType, ModuleType]:
    failures: list[str] = []
    seen: set[Path] = set()
    for directory in _candidate_stub_directories(explicit):
        resolved_directory = directory.resolve()
        if resolved_directory in seen or not resolved_directory.exists():
            continue
        seen.add(resolved_directory)
        path = str(resolved_directory)
        if path not in sys.path:
            sys.path.insert(0, path)
        try:
            pb2 = importlib.import_module("freeplane_pb2")
            pb2_grpc = importlib.import_module("freeplane_pb2_grpc")
            return pb2, pb2_grpc
        except ImportError as exc:
            sys.modules.pop("freeplane_pb2", None)
            sys.modules.pop("freeplane_pb2_grpc", None)
            failures.append(f"{resolved_directory}: {exc}")

    details = "\n".join(failures)
    suffix = f"\nTried:\n{details}" if details else ""
    raise GrpcClientError(
        "cannot import freeplane_pb2.py and freeplane_pb2_grpc.py. "
        "Pass --grpc-stubs-dir or set FREEPLANE_GRPC_PYTHON_PATH to "
        "freeplane_plugin_grpc/grpc/python." + suffix
    )


def _iter_node_ids(root: RawNode) -> list[str]:
    result: list[str] = []

    def walk(node: RawNode) -> None:
        result.append(node.id)
        for child in node.children:
            walk(child)

    walk(root)
    return result


def _details_groovy(node_ids: list[str]) -> str:
    ids_json = json.dumps(node_ids, ensure_ascii=False)
    return f"""
import groovy.json.JsonOutput

def ids = {ids_json}
def out = [:]
ids.each {{ id ->
    try {{
        def node = N(id)
        if (node != null) {{
            def details = node.detailsText
            if (details != null && details.toString() != "") {{
                out[id] = details.toString()
            }}
        }}
    }} catch (Exception ignored) {{}}
}}
def result = JsonOutput.toJson(out)
println(result)
return result
""".strip()


def _apply_details(node: dict[str, Any], details_by_id: dict[str, Any]) -> None:
    node_id = str(node.get("id", ""))
    details = details_by_id.get(node_id)
    if details not in (None, ""):
        node["detail"] = sanitize_details_text(str(details))
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _apply_details(child, details_by_id)


def fetch_live_map(
    *,
    address: str,
    timeout: float,
    use_groovy_details: bool,
    grpc_stubs_dir: Path | None,
) -> dict[str, Any]:
    try:
        import grpc
    except ImportError as exc:
        raise GrpcClientError("grpcio is required for live Freeplane export") from exc

    pb2, pb2_grpc = _load_stubs(grpc_stubs_dir)
    channel = grpc.insecure_channel(address)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout)
        stub = pb2_grpc.FreeplaneStub(channel)
        response = stub.MindMapToJSON(pb2.MindMapToJSONRequest())
        if hasattr(response, "success") and not response.success:
            raise GrpcClientError("MindMapToJSON returned success=false")

        parsed = _extract_json_value(getattr(response, "json", "") or "")
        if not isinstance(parsed, dict):
            raise GrpcClientError("MindMapToJSON returned invalid JSON")

        if use_groovy_details:
            root = RawNode.model_validate(parsed)
            groovy_response = stub.Groovy(
                pb2.GroovyRequest(groovy_code=_details_groovy(_iter_node_ids(root)))
            )
            if getattr(groovy_response, "success", True):
                details = _extract_json_value(getattr(groovy_response, "result", "") or "")
                if isinstance(details, dict):
                    _apply_details(parsed, details)
        return parsed
    except grpc.FutureTimeoutError as exc:
        raise GrpcClientError(
            f"Freeplane gRPC server at {address} did not become ready within {timeout:g}s"
        ) from exc
    finally:
        channel.close()
