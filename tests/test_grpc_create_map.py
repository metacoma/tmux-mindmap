from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from freeplane_tmux.grpc_client import GrpcClientError, create_live_map


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


def test_create_live_map_calls_groovy_with_clean_root_script(monkeypatch) -> None:
    response = SimpleNamespace(
        success=True,
        result='{"name":"Operations","root_text":"Operations"}',
        error_message="",
    )
    calls = _install_fake_grpc(monkeypatch, response=response)
    monkeypatch.setattr(
        "freeplane_tmux.grpc_client._load_stubs",
        lambda: (calls["pb2"], calls["pb2_grpc"]),
    )

    result = create_live_map(
        address="freeplane.example:50052",
        timeout=3.5,
        map_name="Operations",
        terminal_command="gnome-terminal --",
        load_command=[
            "/tmp/freeplane-tmux",
            "--addr",
            "freeplane.example:50052",
            "--load",
        ],
    )

    source = calls["request"].groovy_code
    assert result == "Operations"
    assert calls["address"] == "freeplane.example:50052"
    assert calls["ready_timeout"] == 3.5
    assert calls["rpc_timeout"] == 3.5
    assert "c.newMap()" in source
    assert "newMap.root['script1'] = rootScript" in source
    assert 'def helloWindow = newMap.root.createChild("hello-win")' in source
    assert 'def helloCommand = helloWindow.createChild("echo hello world")' in source
    assert 'helloWindow.tags.add("WINDOW")' in source
    assert "terminalCommand" in source
    assert "loadCommand" in source
    assert "--launch-gui-terminal" not in source
    assert "launcherScriptBase64" not in source
    assert calls["closed"] is True


def test_create_live_map_reports_groovy_failure(monkeypatch) -> None:
    response = SimpleNamespace(success=False, result="", error_message="permission denied")
    calls = _install_fake_grpc(monkeypatch, response=response)
    monkeypatch.setattr(
        "freeplane_tmux.grpc_client._load_stubs",
        lambda: (calls["pb2"], calls["pb2_grpc"]),
    )

    with pytest.raises(GrpcClientError, match="permission denied"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            map_name="Operations",
            terminal_command=None,
            load_command=["/tmp/freeplane-tmux", "--load"],
        )


def test_create_live_map_rejects_empty_name() -> None:
    with pytest.raises(GrpcClientError, match="must not be empty"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            map_name="   ",
            terminal_command=None,
            load_command=["/tmp/freeplane-tmux", "--load"],
        )


def test_create_live_map_rejects_empty_load_command() -> None:
    with pytest.raises(GrpcClientError, match="load command must not be empty"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            map_name="Operations",
            terminal_command=None,
            load_command=[],
        )


def test_create_live_map_rejects_invalid_terminal_command() -> None:
    with pytest.raises(GrpcClientError, match="invalid create-terminal"):
        create_live_map(
            address="127.0.0.1:50051",
            timeout=1.0,
            map_name="Operations",
            terminal_command='unclosed "quote',
            load_command=["/tmp/freeplane-tmux", "--load"],
        )


def test_load_stubs_returns_bundled_modules() -> None:
    from freeplane_tmux.grpc_client import _load_stubs

    pb2, pb2_grpc = _load_stubs()

    assert pb2.GroovyRequest(groovy_code="x").groovy_code == "x"
    assert hasattr(pb2_grpc.FreeplaneStub, "__init__")


def test_details_groovy_uses_freeplane_plain_text_api() -> None:
    from freeplane_tmux.grpc_client import _details_groovy

    source = _details_groovy(["node-1"])

    assert "node.details?.plain" in source
    assert "node.detailsText" not in source


def test_bundled_stubs_cover_current_node_rpc() -> None:
    from freeplane_tmux.grpc_client import _load_stubs

    pb2, pb2_grpc = _load_stubs()

    assert pb2.GetCurrentNodeRequest().SerializeToString() == b""
    assert hasattr(pb2_grpc.FreeplaneStub, "__init__")
