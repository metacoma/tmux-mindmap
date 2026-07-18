from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path
from typing import Any

from .launcher import TERMINAL_COMMAND_B64_FLAG, encode_terminal_command
from .models import RawNode
from .text import sanitize_details_text
from .vendor import freeplane_pb2, freeplane_pb2_grpc


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


def _load_stubs(_: Path | None = None):
    return freeplane_pb2, freeplane_pb2_grpc


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


def _map_local_script(
    launcher_binary_path: str,
    terminal_command: str | None = None,
) -> str:
    binary_json = json.dumps(launcher_binary_path, ensure_ascii=False)
    terminal_command_b64_json = json.dumps(
        encode_terminal_command(terminal_command), ensure_ascii=False
    )
    terminal_parts = shlex.split(terminal_command or "")
    terminal_parts_json = json.dumps(terminal_parts, ensure_ascii=False)
    terminal_command_flag_json = json.dumps(TERMINAL_COMMAND_B64_FLAG, ensure_ascii=False)
    return f"""
// @ExecutionModes({{ON_SELECTED_NODE}})
// @Permission_granted EXEC("execute external process")
// @Permission_granted READ("read files")

def freeplaneTmuxBinary = {binary_json}
def terminalCommandB64 = {terminal_command_b64_json}
def terminalParts = {terminal_parts_json}
def terminalCommandFlag = {terminal_command_flag_json}

def binaryFile = new File(freeplaneTmuxBinary)
if (!binaryFile.isFile()) {{
    throw new RuntimeException("freeplane-tmux binary not found: ${{binaryFile.absolutePath}}")
}}
if (!binaryFile.canExecute()) {{
    throw new RuntimeException(
        "freeplane-tmux binary is not executable: ${{binaryFile.absolutePath}}"
    )
}}

def hasGui = System.getenv("DISPLAY") || System.getenv("WAYLAND_DISPLAY")
if (!hasGui) {{
    throw new RuntimeException("No GUI display detected (DISPLAY/WAYLAND_DISPLAY is not set)")
}}

def cmd = [binaryFile.absolutePath, "--_launch-gui-terminal", "--load"]
if (terminalCommandB64) {{
    cmd.add(terminalCommandFlag + "=" + terminalCommandB64)
}} else {{
    terminalParts.each {{
        cmd.add("--terminal-part=" + it.toString())
    }}
}}
def pb = new ProcessBuilder(cmd.collect {{ it.toString() }})
pb.redirectErrorStream(true)
pb.start()

c.statusInfo = "Started tmux launcher"
""".strip()


def _create_map_groovy(
    map_name: str,
    launcher_binary_path: str,
    terminal_command: str | None = None,
) -> str:
    encoded_name = json.dumps(map_name, ensure_ascii=False)
    launcher_script_base64 = base64.b64encode(
        _map_local_script(launcher_binary_path, terminal_command).encode("utf-8")
    ).decode("ascii")
    encoded_script_base64 = json.dumps(launcher_script_base64, ensure_ascii=False)
    return f"""
import groovy.json.JsonOutput

def mapName = {encoded_name}
def launcherScriptBase64 = {encoded_script_base64}
def launcherScript = new String(launcherScriptBase64.decodeBase64(), "UTF-8")
def newMap = c.newMap()
if (newMap == null) {{
    throw new IllegalStateException("Freeplane failed to create a new map")
}}
newMap.name = mapName
newMap.root.text = mapName
newMap.root['script1'] = launcherScript
return JsonOutput.toJson([
    name: newMap.name,
    root_text: newMap.root.text,
    script1: newMap.root['script1']?.toString(),
])
""".strip()


def create_live_map(
    *,
    address: str,
    timeout: float,
    grpc_stubs_dir: Path | None,
    map_name: str,
    launcher_binary_path: str,
    terminal_command: str | None = None,
) -> str:
    """Create a new unsaved Freeplane map and return its effective name."""

    normalized_name = map_name.strip()
    if not normalized_name:
        raise GrpcClientError("map name must not be empty")

    normalized_binary_path = launcher_binary_path.strip()
    if not normalized_binary_path:
        raise GrpcClientError("launcher binary path must not be empty")

    try:
        shlex.split(terminal_command or "")
    except ValueError as exc:
        raise GrpcClientError(f"invalid create-terminal: {exc}") from exc

    try:
        import grpc
    except ImportError as exc:
        raise GrpcClientError("grpcio is required for live Freeplane operations") from exc

    pb2, pb2_grpc = _load_stubs(grpc_stubs_dir)
    channel = grpc.insecure_channel(address)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout)
        stub = pb2_grpc.FreeplaneStub(channel)
        response = stub.Groovy(
            pb2.GroovyRequest(
                groovy_code=_create_map_groovy(
                    normalized_name,
                    normalized_binary_path,
                    terminal_command,
                )
            ),
            timeout=timeout,
        )
        if not getattr(response, "success", False):
            error = getattr(response, "error_message", "") or "Groovy returned success=false"
            raise GrpcClientError(f"cannot create Freeplane map: {error}")

        result = _extract_json_value(getattr(response, "result", "") or "")
        if isinstance(result, dict):
            effective_name = str(result.get("name", "")).strip()
            if effective_name:
                return effective_name
        return normalized_name
    except grpc.FutureTimeoutError as exc:
        raise GrpcClientError(
            f"Freeplane gRPC server at {address} did not become ready within {timeout:g}s"
        ) from exc
    finally:
        channel.close()


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
