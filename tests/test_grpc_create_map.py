from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from freeplane_tmux.grpc_client import (
    GrpcClientError,
    _create_map_groovy,
    _map_local_script,
    create_live_map,
)


def test_map_local_script_contains_dynamic_binary_path() -> None:
    script = _map_local_script(
        "/opt/freeplane-tmux/bin/freeplane-tmux",
        "gnome-terminal --",
    )

    assert "// @ExecutionModes({ON_SELECTED_NODE})" in script
    assert "bin/freeplane_tmux_launcher.sh" in script
    assert "--freeplane-tmux-bin" in script
    assert "--terminal-part" in script
    assert "binaryFile.absolutePath" in script
    assert "/opt/freeplane-tmux/bin/freeplane-tmux" in script
    assert 'terminalParts = ["gnome-terminal", "--"]' in script


def test_create_map_groovy_quotes_untrusted_name_and_sets_script1() -> None:
    name = 'ops "map"\nnewMap.name = "injected"'
    script = _create_map_groovy(name, "/tmp/freeplane-tmux", "gnome-terminal --")

    assert f"def mapName = {json.dumps(name, ensure_ascii=False)}" in script
    assert script.count("newMap.name = mapName") == 1
    assert 'newMap.name = "injected"' not in script
    assert "newMap.root['script1'] = launcherScript" in script
    assert "launcherScriptBase64 = " in script
    assert "/tmp/freeplane-tmux" not in script
    assert "gnome-terminal" not in script
    assert "${shellScript.absolutePath}" not in script


def _install_fake_grpc(monkeypatch, *, response: object):
    calls: dict[str, object] = {}

    class ReadyFuture:
        def result(self, timeout: float) -> None:
            calls["ready_timeout"] = timeout

    class Channel:
        def close(self) -> None:
            calls["closed"] = True

    grpc_module = ModuleType("grpc")
    grpc_module.FutureTimeoutError = type("FutureTimeoutError", (Exception,), {})
    channel = Channel()

    def insecure_channel(address: str) -> Channel:
        calls["address"] = address
        return channel

    grpc_module.insecure_channel = insecure_channel
    grpc_module.channel_ready_future = lambda value: ReadyFuture()
    monkeypatch.setitem(sys.modules, "grpc", grpc_module)

    class Stub:
        def __init__(self, value: object) -> None:
            calls["channel"] = value

        def Groovy(self, request: object, *, timeout: float) -> object:
            calls["request"] = request
            calls["rpc_timeout"] = timeout
            return response

    pb2 = SimpleNamespace(GroovyRequest=lambda **kwargs: SimpleNamespace(**kwargs))
    pb2_grpc = SimpleNamespace(FreeplaneStub=Stub)
    calls["pb2"] = pb2
    calls["pb2_grpc"] = pb2_grpc
    return calls


def test_create_live_map_calls_groovy(monkeypatch) -> None:
    response = SimpleNamespace(
        success=True,
        result='{"name":"Operations","root_text":"Operations"}',
        error_message="",
    )
    calls = _install_fake_grpc(monkeypatch, response=response)
    monkeypatch.setattr(
        "freeplane_tmux.grpc_client._load_stubs",
        lambda explicit=None: (calls["pb2"], calls["pb2_grpc"]),
    )

    result = create_live_map(
        address="freeplane.example:50052",
        timeout=3.5,
        grpc_stubs_dir=None,
        map_name="Operations",
        launcher_binary_path="/tmp/freeplane-tmux",
        terminal_command="gnome-terminal --",
    )

    assert result == "Operations"
    assert calls["address"] == "freeplane.example:50052"
    assert calls["ready_timeout"] == 3.5
    assert calls["rpc_timeout"] == 3.5
    assert "c.newMap()" in calls["request"].groovy_code
    assert "newMap.root['script1'] = launcherScript" in calls["request"].groovy_code
    assert "launcherScriptBase64" in calls["request"].groovy_code
    assert "/tmp/freeplane-tmux" not in calls["request"].groovy_code
    assert "gnome-terminal" not in calls["request"].groovy_code
    assert calls["closed"] is True


def test_create_live_map_reports_groovy_failure(monkeypatch) -> None:
    response = SimpleNamespace(success=False, result="", error_message="permission denied")
    calls = _install_fake_grpc(monkeypatch, response=response)
    monkeypatch.setattr(
        "freeplane_tmux.grpc_client._load_stubs",
        lambda explicit=None: (calls["pb2"], calls["pb2_grpc"]),
    )

    with pytest.raises(GrpcClientError, match="permission denied"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            grpc_stubs_dir=None,
            map_name="Operations",
            launcher_binary_path="/tmp/freeplane-tmux",
        )


def test_create_live_map_rejects_empty_name() -> None:
    with pytest.raises(GrpcClientError, match="must not be empty"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            grpc_stubs_dir=None,
            map_name="   ",
            launcher_binary_path="/tmp/freeplane-tmux",
        )


def test_create_live_map_rejects_empty_binary_path() -> None:
    with pytest.raises(GrpcClientError, match="binary path must not be empty"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            grpc_stubs_dir=None,
            map_name="Operations",
            launcher_binary_path="   ",
        )


def test_create_live_map_rejects_invalid_terminal_command() -> None:
    with pytest.raises(GrpcClientError, match="invalid create-terminal"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            grpc_stubs_dir=None,
            map_name="Operations",
            launcher_binary_path="/tmp/freeplane-tmux",
            terminal_command='unclosed "quote',
        )


def test_load_stubs_returns_bundled_modules() -> None:
    from freeplane_tmux.grpc_client import _load_stubs

    pb2, pb2_grpc = _load_stubs(None)

    assert pb2.GroovyRequest(groovy_code="x").groovy_code == "x"
    assert hasattr(pb2_grpc.FreeplaneStub, "__init__")
